from __future__ import annotations

import csv
import heapq
import io
import json
import math
import re
import sqlite3
import time
from collections import Counter, defaultdict
from typing import Any

from .embeddings import cosine, dumps_embedding, embed_texts, loads_embedding, tokenize
from .progress import update_task


ACTION_WEIGHTS = {
    "relevant": 0.12,
    "want_to_read": 0.10,
    "read": 0.05,
    "not_relevant": -0.14,
    "hide": -0.35,
}


def _text(row: sqlite3.Row) -> str:
    return " ".join(
        str(row[key] or "")
        for key in row.keys()
        if key in {"title", "abstract", "keywords", "topic", "decision", "eventtype"}
    )


def _bm25_like(query_terms: list[str], doc_terms: list[str]) -> float:
    if not query_terms or not doc_terms:
        return 0.0
    query = Counter(query_terms)
    doc = Counter(doc_terms)
    overlap = 0.0
    for term, q_count in query.items():
        if term in doc:
            overlap += min(q_count, doc[term]) / math.sqrt(doc[term])
    denom = math.sqrt(len(query_terms)) + math.sqrt(len(doc_terms))
    return min(1.0, overlap / denom * 4.0)


def _tag_overlap(keywords: list[str], doc_text: str) -> float:
    if not keywords:
        return 0.0
    doc = doc_text.lower()
    hits = sum(1 for keyword in keywords if keyword.lower() in doc)
    return min(1.0, hits / max(1, min(len(keywords), 5)))


def _feedback_adjustment(conn: sqlite3.Connection, paper_id: int, profile_id: int) -> float:
    rows = conn.execute(
        """
        SELECT action FROM feedback
        WHERE paper_id = ? AND (profile_id = ? OR profile_id IS NULL)
        ORDER BY created_at DESC LIMIT 5
        """,
        (paper_id, profile_id),
    ).fetchall()
    return max(-0.35, min(0.2, sum(ACTION_WEIGHTS.get(row["action"], 0.0) for row in rows)))


def _matched_zotero_from_cache(zotero_rows: list[dict[str, Any]], paper_embedding: list[float], profile_name: str, limit: int = 3) -> list[dict[str, Any]]:
    if profile_name == "All Zotero":
        rows = zotero_rows
    else:
        rows = [row for row in zotero_rows if profile_name in row["tags"]]
        if not rows:
            rows = zotero_rows
    scored = []
    for row in rows:
        score = cosine(paper_embedding, row["embedding"])
        scored.append({"id": row["id"], "title": row["title"], "score": round(score, 4)})
    return sorted(scored, key=lambda item: item["score"], reverse=True)[:limit]


def _load_zotero_cache(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, title, tags, embedding FROM zotero_items").fetchall()
    cache = []
    for row in rows:
        try:
            tags = json.loads(row["tags"] or "[]")
        except json.JSONDecodeError:
            tags = []
        cache.append({"id": row["id"], "title": row["title"], "tags": tags, "embedding": loads_embedding(row["embedding"])})
    return cache


def _load_feedback_cache(conn: sqlite3.Connection) -> dict[tuple[int, int | None], float]:
    rows = conn.execute("SELECT paper_id, profile_id, action FROM feedback ORDER BY created_at DESC").fetchall()
    grouped: dict[tuple[int, int | None], list[str]] = defaultdict(list)
    for row in rows:
        key = (row["paper_id"], row["profile_id"])
        if len(grouped[key]) < 5:
            grouped[key].append(row["action"])
    return {
        key: max(-0.35, min(0.2, sum(ACTION_WEIGHTS.get(action, 0.0) for action in actions)))
        for key, actions in grouped.items()
    }


def _feedback_from_cache(cache: dict[tuple[int, int | None], float], paper_id: int, profile_id: int) -> float:
    return max(-0.35, min(0.2, cache.get((paper_id, profile_id), 0.0) + cache.get((paper_id, None), 0.0)))


def _execute_locked_retry(conn: sqlite3.Connection, fn, attempts: int = 6):
    delay = 0.15
    for attempt in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay *= 1.8


def run_matching(conn: sqlite3.Connection, conference: str, year: int, limit_per_profile: int = 100) -> dict[str, Any]:
    profiles = conn.execute("SELECT * FROM interest_profiles ORDER BY name").fetchall()
    paper_rows = conn.execute(
        "SELECT * FROM conference_papers WHERE conference = ? AND year = ? ORDER BY id",
        (conference.lower(), year),
    ).fetchall()
    if not profiles:
        raise ValueError("No interest profiles found. Import Zotero BibTeX first.")
    if not paper_rows:
        raise ValueError(f"No papers found for {conference.upper()} {year}. Import a conference CSV first.")
    papers = []
    for paper in paper_rows:
        doc_text = _text(paper)
        papers.append(
            {
                "row": paper,
                "embedding": loads_embedding(paper["embedding"]),
                "doc_text": doc_text,
                "tokens": tokenize(doc_text),
            }
        )
    zotero_cache = _load_zotero_cache(conn)
    feedback_cache = _load_feedback_cache(conn)
    settings = {
        "weights": {"embedding": 0.55, "bm25": 0.25, "tag": 0.10, "feedback": 0.10},
        "limit_per_profile": limit_per_profile,
        "ranking_mode": "two_stage_topk_evidence",
    }
    results_to_insert = []
    total_steps = len(profiles) * len(papers)
    done_steps = 0
    for profile_index, profile in enumerate(profiles, start=1):
        profile_embedding = loads_embedding(profile["centroid_embedding"])
        keywords = json.loads(profile["keywords"] or "[]")
        query_terms = tokenize(" ".join(keywords) or profile["name"])
        scored = []
        for paper_data in papers:
            paper = paper_data["row"]
            embedding_score = max(0.0, cosine(profile_embedding, paper_data["embedding"]))
            bm25_score = _bm25_like(query_terms, paper_data["tokens"])
            tag_score = _tag_overlap(keywords, paper_data["doc_text"])
            feedback_score = _feedback_from_cache(feedback_cache, paper["id"], profile["id"])
            final = 0.55 * embedding_score + 0.25 * bm25_score + 0.10 * tag_score + 0.10 * feedback_score
            scored.append((final, paper["id"], paper_data, embedding_score, bm25_score, tag_score, feedback_score))
            done_steps += 1
            if done_steps == total_steps or done_steps % 1000 == 0:
                update_task(
                    "scoring",
                    done_steps,
                    total_steps,
                    f"Scoring profile {profile_index}/{len(profiles)}: {profile['name']}",
                )
        top = heapq.nlargest(limit_per_profile, scored, key=lambda item: (item[0], -item[1]))
        update_task("evidence", done_steps, total_steps, f"Building evidence for {profile['name']}")
        for final, _paper_id, paper_data, embedding_score, bm25_score, tag_score, feedback_score in top:
            paper = paper_data["row"]
            matched = _matched_zotero_from_cache(zotero_cache, paper_data["embedding"], profile["name"])
            reason = _build_reason(profile["name"], paper["title"], embedding_score, bm25_score, tag_score, matched)
            results_to_insert.append(
                (
                    paper["id"],
                    profile["id"],
                    round(final, 6),
                    round(embedding_score, 6),
                    round(bm25_score, 6),
                    round(tag_score, 6),
                    round(feedback_score, 6),
                    json.dumps(matched, ensure_ascii=False),
                    reason,
                )
            )

    def write_results():
        with conn:
            run_id = conn.execute(
                "INSERT INTO match_runs(conference, year, settings) VALUES (?, ?, ?)",
                (conference.lower(), year, json.dumps(settings)),
            ).lastrowid
            conn.executemany(
                """
                INSERT INTO match_results(
                    run_id, paper_id, profile_id, score, embedding_score, bm25_score,
                    tag_score, feedback_score, matched_zotero_items, reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [(run_id, *row) for row in results_to_insert],
            )
            return run_id

    update_task("saving_matches", total_steps, total_steps, f"Saving {len(results_to_insert)} match results")
    run_id = _execute_locked_retry(conn, write_results)
    return {"run_id": run_id, "profiles": len(profiles), "papers": len(papers), "results": len(results_to_insert)}


def upsert_custom_profile(conn: sqlite3.Connection, name: str, description: str, keywords: list[str] | None = None) -> dict[str, Any]:
    clean_name = name.strip()
    clean_description = description.strip()
    if not clean_name:
        raise ValueError("Profile name is required.")
    if not clean_description:
        raise ValueError("Interest description is required.")
    keyword_list = keywords or tokenize(clean_description)[:16]
    embedding = embed_texts([f"{clean_name}\n{clean_description}\n{' '.join(keyword_list)}"])[0]
    with conn:
        conn.execute(
            """
            INSERT INTO interest_profiles(name, source_type, keywords, centroid_embedding, item_count, updated_at)
            VALUES (?, 'custom_text', ?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(name) DO UPDATE SET
                source_type='custom_text',
                keywords=excluded.keywords,
                centroid_embedding=excluded.centroid_embedding,
                item_count=1,
                updated_at=CURRENT_TIMESTAMP
            """,
            (clean_name, json.dumps(keyword_list, ensure_ascii=False), dumps_embedding(embedding)),
        )
    row = conn.execute("SELECT * FROM interest_profiles WHERE name = ?", (clean_name,)).fetchone()
    return dict(row)


def match_custom_text(conn: sqlite3.Connection, text: str, conference: str | None, year: int | None, limit: int = 50) -> list[dict[str, Any]]:
    clean_text = text.strip()
    if not clean_text:
        raise ValueError("Interest text is required.")
    query_embedding = embed_texts([clean_text])[0]
    query_terms = tokenize(clean_text)
    conditions = []
    params: list[Any] = []
    if conference:
        conditions.append("conference = ?")
        params.append(conference.lower())
    if year:
        conditions.append("year = ?")
        params.append(year)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    papers = conn.execute(f"SELECT * FROM conference_papers {where} ORDER BY id", params).fetchall()
    if not papers:
        raise ValueError("No conference papers found for this query. Import conference CSV first.")
    zotero_cache = _load_zotero_cache(conn)
    scored = []
    for paper in papers:
        doc_text = _text(paper)
        paper_embedding = loads_embedding(paper["embedding"])
        embedding_score = max(0.0, cosine(query_embedding, paper_embedding))
        bm25_score = _bm25_like(query_terms, tokenize(doc_text))
        tag_score = 0.0
        final = 0.72 * embedding_score + 0.28 * bm25_score
        scored.append(
            (final, paper["id"], paper, paper_embedding, embedding_score, bm25_score, tag_score)
        )
    top = heapq.nlargest(max(1, min(limit, 500)), scored, key=lambda item: (item[0], -item[1]))
    results = []
    for final, _paper_id, paper, paper_embedding, embedding_score, bm25_score, tag_score in top:
        matched = _matched_zotero_from_cache(zotero_cache, paper_embedding, "All Zotero")
        reason = _build_reason("临时兴趣", paper["title"], embedding_score, bm25_score, tag_score, matched)
        results.append(
            {
                "id": None,
                "paper_id": paper["id"],
                "profile_id": None,
                "profile_name": "临时兴趣",
                "score": round(final, 6),
                "embedding_score": round(embedding_score, 6),
                "bm25_score": round(bm25_score, 6),
                "tag_score": 0.0,
                "feedback_score": 0.0,
                "matched_zotero_items": matched,
                "reason": reason,
                "title": paper["title"],
                "abstract": paper["abstract"],
                "authors": paper["authors"],
                "conference": paper["conference"],
                "year": paper["year"],
                "url": paper["url"],
                "pdf_url": paper["pdf_url"],
                "decision": paper["decision"],
                "eventtype": paper["eventtype"],
            }
        )
    return _annotate_in_zotero(conn, results)


def match_profile_dynamic(
    conn: sqlite3.Connection,
    profile_id: int,
    conference: str | None,
    year: int | None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    profile = conn.execute("SELECT * FROM interest_profiles WHERE id = ?", (profile_id,)).fetchone()
    if not profile:
        return []
    try:
        keywords = json.loads(profile["keywords"] or "[]")
    except json.JSONDecodeError:
        keywords = []
    profile_embedding = loads_embedding(profile["centroid_embedding"])
    query_terms = tokenize(" ".join(keywords) or profile["name"])
    conditions = []
    params: list[Any] = []
    if conference:
        conditions.append("conference = ?")
        params.append(conference.lower())
    if year:
        conditions.append("year = ?")
        params.append(year)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    papers = conn.execute(f"SELECT * FROM conference_papers {where} ORDER BY id", params).fetchall()
    if not papers:
        return []

    zotero_cache = _load_zotero_cache(conn)
    feedback_cache = _load_feedback_cache(conn)
    scored = []
    for paper in papers:
        doc_text = _text(paper)
        paper_embedding = loads_embedding(paper["embedding"])
        embedding_score = max(0.0, cosine(profile_embedding, paper_embedding))
        bm25_score = _bm25_like(query_terms, tokenize(doc_text))
        tag_score = _tag_overlap(keywords, doc_text)
        feedback_score = _feedback_from_cache(feedback_cache, paper["id"], profile["id"])
        final = 0.55 * embedding_score + 0.25 * bm25_score + 0.10 * tag_score + 0.10 * feedback_score
        scored.append((final, paper["id"], paper, paper_embedding, embedding_score, bm25_score, tag_score, feedback_score))

    top = heapq.nlargest(max(1, min(limit, 1000)), scored, key=lambda item: (item[0], -item[1]))
    results = []
    for final, paper_id, paper, paper_embedding, embedding_score, bm25_score, tag_score, feedback_score in top:
        matched = _matched_zotero_from_cache(zotero_cache, paper_embedding, profile["name"])
        reason = _build_reason(profile["name"], paper["title"], embedding_score, bm25_score, tag_score, matched)
        latest_feedback = _latest_feedback(conn, paper_id, profile["id"])
        results.append(
            {
                "id": f"dynamic-{profile['id']}-{paper['id']}",
                "paper_id": paper["id"],
                "profile_id": profile["id"],
                "profile_name": profile["name"],
                "score": round(final, 6),
                "embedding_score": round(embedding_score, 6),
                "bm25_score": round(bm25_score, 6),
                "tag_score": round(tag_score, 6),
                "feedback_score": round(feedback_score, 6),
                "matched_zotero_items": matched,
                "reason": reason,
                "title": paper["title"],
                "abstract": paper["abstract"],
                "authors": paper["authors"],
                "conference": paper["conference"],
                "year": paper["year"],
                "url": paper["url"],
                "pdf_url": paper["pdf_url"],
                "decision": paper["decision"],
                "eventtype": paper["eventtype"],
                "feedback_action": latest_feedback["action"] if latest_feedback else None,
                "feedback_at": latest_feedback["created_at"] if latest_feedback else None,
                "dynamic": True,
            }
        )
    return results


def _latest_feedback(conn: sqlite3.Connection, paper_id: int, profile_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT action, created_at
        FROM feedback
        WHERE paper_id = ? AND (profile_id = ? OR profile_id IS NULL)
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (paper_id, profile_id),
    ).fetchone()


def _build_reason(profile_name: str, paper_title: str, embedding: float, bm25: float, tag: float, matched: list[dict[str, Any]]) -> str:
    evidence = []
    if embedding > 0.35:
        evidence.append("semantic similarity is high")
    if bm25 > 0.12:
        evidence.append("profile terms overlap with title/abstract")
    if tag > 0:
        evidence.append("profile tag appears in the paper metadata")
    if matched:
        evidence.append(f"closest Zotero item: {matched[0]['title']}")
    if not evidence:
        evidence.append("ranked by combined local similarity signals")
    return f"{paper_title} matches profile '{profile_name}' because " + "; ".join(evidence) + "."


def list_matches(
    conn: sqlite3.Connection,
    profile_id: int | None,
    conference: str | None,
    year: int | None,
    limit: int,
    action: str | None = None,
) -> list[dict[str, Any]]:
    conditions = [
        """
        mr.run_id IN (
            SELECT MAX(id)
            FROM match_runs
            GROUP BY conference, year
        )
        """
    ]
    params: list[Any] = []
    if profile_id:
        conditions.append("mr.profile_id = ?")
        params.append(profile_id)
    if conference:
        conditions.append("cp.conference = ?")
        params.append(conference.lower())
    if year:
        conditions.append("cp.year = ?")
        params.append(year)
    if action:
        conditions.append("lf.action = ?")
        params.append(action)
    rows = conn.execute(
        f"""
        SELECT mr.*, cp.title, cp.abstract, cp.authors, cp.conference, cp.year, cp.url, cp.pdf_url,
               cp.decision, cp.eventtype, ip.name AS profile_name,
               lf.action AS feedback_action, lf.created_at AS feedback_at
        FROM match_results mr
        JOIN conference_papers cp ON cp.id = mr.paper_id
        JOIN interest_profiles ip ON ip.id = mr.profile_id
        LEFT JOIN feedback lf ON lf.id = (
            SELECT f.id
            FROM feedback f
            WHERE f.paper_id = cp.id
              AND (f.profile_id = mr.profile_id OR f.profile_id IS NULL)
            ORDER BY f.created_at DESC, f.id DESC
            LIMIT 1
        )
        WHERE {' AND '.join(conditions)}
        ORDER BY mr.score DESC, mr.id ASC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    items = [_match_row_to_dict(row) for row in rows]
    if not items and profile_id and not action:
        items = match_profile_dynamic(conn, profile_id, conference, year, limit)
    return _annotate_in_zotero(conn, items)


def _match_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["matched_zotero_items"] = json.loads(data.get("matched_zotero_items") or "[]")
    return data


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _annotate_in_zotero(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark each match that already exists in the local Zotero library (title match)."""
    if not items:
        return items
    index = {
        _normalize_title(row["title"])
        for row in conn.execute("SELECT title FROM zotero_items").fetchall()
        if row["title"]
    }
    for item in items:
        item["in_zotero"] = _normalize_title(item.get("title", "")) in index
    return items


def export_matches_csv(conn: sqlite3.Connection) -> str:
    rows = list_matches(conn, None, None, None, 10000)
    output = io.StringIO()
    fields = [
        "profile_name",
        "score",
        "embedding_score",
        "bm25_score",
        "tag_score",
        "feedback_score",
        "conference",
        "year",
        "title",
        "url",
        "reason",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in fields})
    return output.getvalue()
