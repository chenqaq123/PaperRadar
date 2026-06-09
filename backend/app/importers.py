from __future__ import annotations

import csv
import json
import re
import sqlite3
from pathlib import Path
from dataclasses import dataclass
from io import StringIO
from typing import Any

from .embeddings import dumps_embedding, embed_texts, loads_embedding, mean_embedding
from .progress import update_task


@dataclass
class ImportSummary:
    imported: int
    total: int
    detail: str


def _clean_latex(value: str) -> str:
    value = value.replace("\n", " ")
    value = re.sub(r"[{}]", "", value)
    value = value.replace("\\&", "&")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def parse_bibtex(text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    idx = 0
    while True:
        match = re.search(r"@\w+\s*[{(]", text[idx:])
        if not match:
            break
        start = idx + match.start()
        open_pos = idx + match.end() - 1
        open_char = text[open_pos]
        close_char = "}" if open_char == "{" else ")"
        depth = 0
        in_quote = False
        end = open_pos
        while end < len(text):
            char = text[end]
            prev = text[end - 1] if end > 0 else ""
            if char == '"' and prev != "\\":
                in_quote = not in_quote
            if not in_quote:
                if char == open_char:
                    depth += 1
                elif char == close_char:
                    depth -= 1
                    if depth == 0:
                        break
            end += 1
        body = text[open_pos + 1 : end]
        idx = end + 1
        if "," not in body:
            continue
        key, fields_text = body.split(",", 1)
        entry: dict[str, str] = {"source_key": key.strip()}
        field_idx = 0
        while field_idx < len(fields_text):
            name_match = re.search(r"([A-Za-z][A-Za-z0-9_\-]*)\s*=", fields_text[field_idx:])
            if not name_match:
                break
            name = name_match.group(1).lower()
            value_start = field_idx + name_match.end()
            while value_start < len(fields_text) and fields_text[value_start].isspace():
                value_start += 1
            if value_start >= len(fields_text):
                break
            delimiter = fields_text[value_start]
            if delimiter in "{(":
                close = "}" if delimiter == "{" else ")"
                depth = 0
                pos = value_start
                while pos < len(fields_text):
                    if fields_text[pos] == delimiter:
                        depth += 1
                    elif fields_text[pos] == close:
                        depth -= 1
                        if depth == 0:
                            break
                    pos += 1
                value = fields_text[value_start + 1 : pos]
                field_idx = pos + 1
            elif delimiter == '"':
                pos = value_start + 1
                while pos < len(fields_text):
                    if fields_text[pos] == '"' and fields_text[pos - 1] != "\\":
                        break
                    pos += 1
                value = fields_text[value_start + 1 : pos]
                field_idx = pos + 1
            else:
                pos = value_start
                while pos < len(fields_text) and fields_text[pos] != ",":
                    pos += 1
                value = fields_text[value_start:pos]
                field_idx = pos + 1
            entry[name] = _clean_latex(value)
        if entry.get("title"):
            entries.append(entry)
    return entries


def _split_tags(value: str) -> list[str]:
    raw = re.split(r"[;,]", value or "")
    tags = []
    for item in raw:
        item = item.strip()
        if item and item.lower() not in {"none", "null"}:
            tags.append(item)
    return sorted(set(tags), key=str.lower)


def import_zotero_bibtex(conn: sqlite3.Connection, text: str) -> ImportSummary:
    entries = parse_bibtex(text)
    if not entries:
        raise ValueError("No BibTeX entries with titles were found.")
    payload: list[tuple[Any, ...]] = []
    embed_inputs = []
    for entry in entries:
        title = entry.get("title", "")
        abstract = entry.get("abstract", "") or entry.get("annote", "")
        authors = entry.get("author", "")
        year_raw = entry.get("year", "")
        year = int(year_raw) if year_raw.isdigit() else None
        tags = _split_tags(entry.get("keywords", "") or entry.get("tags", ""))
        source_key = entry.get("source_key") or entry.get("doi") or title
        embed_inputs.append(f"{title}\n{abstract}\n{' '.join(tags)}")
        payload.append((title, abstract, authors, year, json.dumps(tags, ensure_ascii=False), source_key))
    embeddings = embed_texts(embed_inputs)
    update_task("writing_zotero", 0, len(payload), "Writing Zotero items to SQLite")
    with conn:
        for index, (row, embedding) in enumerate(zip(payload, embeddings), start=1):
            conn.execute(
                """
                INSERT INTO zotero_items(title, abstract, authors, year, tags, source_key, embedding, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(source_key) DO UPDATE SET
                    title=excluded.title,
                    abstract=excluded.abstract,
                    authors=excluded.authors,
                    year=excluded.year,
                    tags=excluded.tags,
                    embedding=excluded.embedding,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (*row, dumps_embedding(embedding)),
            )
            if index == len(payload) or index % 100 == 0:
                update_task("writing_zotero", index, len(payload), f"Writing Zotero items {index}/{len(payload)}")
    rebuild_interest_profiles(conn)
    return ImportSummary(imported=len(entries), total=len(entries), detail="Imported Zotero BibTeX entries.")


def discover_zotero_sqlite() -> Path | None:
    home = Path.home()
    candidates = [
        home / "Zotero" / "zotero.sqlite",
        home / "Library" / "Application Support" / "Zotero" / "Profiles",
    ]
    direct = candidates[0]
    if direct.exists():
        return direct
    profiles = candidates[1]
    if profiles.exists():
        for path in sorted(profiles.glob("*/zotero/zotero.sqlite")):
            if path.exists():
                return path
    return None


def import_zotero_sqlite(conn: sqlite3.Connection, sqlite_path: str | None = None, collection: str | None = None) -> ImportSummary:
    path = Path(sqlite_path).expanduser() if sqlite_path else discover_zotero_sqlite()
    if not path or not path.exists():
        raise ValueError("Could not find Zotero's local zotero.sqlite. Pass a path manually or check Zotero data directory.")
    source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        entries = _read_zotero_sqlite_items(source, collection)
    finally:
        source.close()
    if not entries:
        raise ValueError("No Zotero items with titles were found in the selected local library/collection.")
    embed_inputs = [f"{item['title']}\n{item['abstract']}\n{' '.join(item['tags'])}\n{' '.join(item['collections'])}" for item in entries]
    embeddings = embed_texts(embed_inputs)
    update_task("writing_zotero", 0, len(entries), "Writing Zotero items to SQLite")
    with conn:
        for index, (item, embedding) in enumerate(zip(entries, embeddings), start=1):
            tags = sorted(set(item["tags"] + item["collections"]), key=str.lower)
            conn.execute(
                """
                INSERT INTO zotero_items(title, abstract, authors, year, tags, source_key, embedding, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(source_key) DO UPDATE SET
                    title=excluded.title,
                    abstract=excluded.abstract,
                    authors=excluded.authors,
                    year=excluded.year,
                    tags=excluded.tags,
                    embedding=excluded.embedding,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    item["title"],
                    item["abstract"],
                    item["authors"],
                    item["year"],
                    json.dumps(tags, ensure_ascii=False),
                    item["source_key"],
                    dumps_embedding(embedding),
                ),
            )
            if index == len(entries) or index % 100 == 0:
                update_task("writing_zotero", index, len(entries), f"Writing Zotero items {index}/{len(entries)}")
    rebuild_interest_profiles(conn)
    return ImportSummary(imported=len(entries), total=len(entries), detail=f"Imported Zotero local library from {path}.")


def _read_zotero_sqlite_items(source: sqlite3.Connection, collection: str | None) -> list[dict[str, Any]]:
    collection_clause = ""
    params: list[Any] = []
    if collection:
        collection_clause = """
            AND i.itemID IN (
                SELECT ci.itemID
                FROM collectionItems ci
                JOIN collections c ON c.collectionID = ci.collectionID
                WHERE c.collectionName = ?
            )
        """
        params.append(collection)
    rows = source.execute(
        f"""
        SELECT i.itemID, i.key, it.typeName
        FROM items i
        JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
        WHERE it.typeName NOT IN ('attachment', 'note', 'annotation')
        {collection_clause}
        ORDER BY i.itemID
        """,
        params,
    ).fetchall()
    item_ids = [row["itemID"] for row in rows]
    field_values = _zotero_field_values(source, item_ids)
    creators = _zotero_creators(source, item_ids)
    tags = _zotero_tags(source, item_ids)
    collections = _zotero_collections(source, item_ids)
    entries = []
    for row in rows:
        values = field_values.get(row["itemID"], {})
        title = values.get("title", "").strip()
        if not title:
            continue
        date = values.get("date", "")
        entries.append(
            {
                "title": title,
                "abstract": values.get("abstractNote", ""),
                "authors": creators.get(row["itemID"], ""),
                "year": _year_from_text(date),
                "tags": tags.get(row["itemID"], []),
                "collections": collections.get(row["itemID"], []),
                "source_key": f"zotero:{row['key']}",
            }
        )
    return entries


def _chunked(values: list[int], size: int = 700) -> list[list[int]]:
    return [values[idx : idx + size] for idx in range(0, len(values), size)]


def _placeholders(count: int) -> str:
    return ",".join("?" for _ in range(count))


def _zotero_field_values(source: sqlite3.Connection, item_ids: list[int]) -> dict[int, dict[str, str]]:
    result: dict[int, dict[str, str]] = {}
    for chunk in _chunked(item_ids):
        if not chunk:
            continue
        rows = source.execute(
            f"""
            SELECT id.itemID, f.fieldName, v.value
            FROM itemData id
            JOIN fields f ON f.fieldID = id.fieldID
            JOIN itemDataValues v ON v.valueID = id.valueID
            WHERE id.itemID IN ({_placeholders(len(chunk))})
            """,
            chunk,
        ).fetchall()
        for row in rows:
            result.setdefault(row["itemID"], {})[row["fieldName"]] = row["value"] or ""
    return result


def _zotero_creators(source: sqlite3.Connection, item_ids: list[int]) -> dict[int, str]:
    result: dict[int, list[tuple[int, str]]] = {}
    for chunk in _chunked(item_ids):
        if not chunk:
            continue
        rows = source.execute(
            f"""
            SELECT ic.itemID, ic.orderIndex, c.firstName, c.lastName, c.fieldMode
            FROM itemCreators ic
            JOIN creators c ON c.creatorID = ic.creatorID
            WHERE ic.itemID IN ({_placeholders(len(chunk))})
            ORDER BY ic.itemID, ic.orderIndex
            """,
            chunk,
        ).fetchall()
        for row in rows:
            if row["fieldMode"] == 1:
                name = row["lastName"] or row["firstName"] or ""
            else:
                name = " ".join(part for part in [row["firstName"], row["lastName"]] if part).strip()
            if name:
                result.setdefault(row["itemID"], []).append((row["orderIndex"], name))
    return {item_id: "; ".join(name for _, name in sorted(names)) for item_id, names in result.items()}


def _zotero_tags(source: sqlite3.Connection, item_ids: list[int]) -> dict[int, list[str]]:
    result: dict[int, list[str]] = {}
    for chunk in _chunked(item_ids):
        if not chunk:
            continue
        rows = source.execute(
            f"""
            SELECT it.itemID, t.name
            FROM itemTags it
            JOIN tags t ON t.tagID = it.tagID
            WHERE it.itemID IN ({_placeholders(len(chunk))})
            """,
            chunk,
        ).fetchall()
        for row in rows:
            if row["name"]:
                result.setdefault(row["itemID"], []).append(row["name"])
    return {item_id: sorted(set(names), key=str.lower) for item_id, names in result.items()}


def _zotero_collections(source: sqlite3.Connection, item_ids: list[int]) -> dict[int, list[str]]:
    result: dict[int, list[str]] = {}
    for chunk in _chunked(item_ids):
        if not chunk:
            continue
        rows = source.execute(
            f"""
            SELECT ci.itemID, c.collectionName
            FROM collectionItems ci
            JOIN collections c ON c.collectionID = ci.collectionID
            WHERE ci.itemID IN ({_placeholders(len(chunk))})
            """,
            chunk,
        ).fetchall()
        for row in rows:
            if row["collectionName"]:
                result.setdefault(row["itemID"], []).append(row["collectionName"])
    return {item_id: sorted(set(names), key=str.lower) for item_id, names in result.items()}


def _year_from_text(value: str) -> int | None:
    match = re.search(r"(19|20)\d{2}", value or "")
    return int(match.group(0)) if match else None


def import_conference_csv(conn: sqlite3.Connection, text: str, conference: str, year: int) -> ImportSummary:
    rows = list(csv.DictReader(StringIO(text)))
    if not rows:
        raise ValueError("CSV is empty or missing a header row.")
    payload: list[tuple[Any, ...]] = []
    embed_inputs = []
    for index, row in enumerate(rows):
        title = (row.get("title") or row.get("paper_title") or "").strip()
        if not title:
            continue
        abstract = (row.get("abstract") or "").strip()
        zh_title = (row.get("zh_title") or "").strip()
        zh_abstract = (row.get("zh_abstract") or "").strip()
        keywords = (row.get("keywords") or row.get("topic") or "").strip()
        external_id = (row.get("id") or row.get("uid") or f"{conference}-{year}-{index}").strip()
        authors = (row.get("authors") or "").strip()
        decision = (row.get("decision") or "").strip()
        eventtype = (row.get("eventtype") or "").strip()
        topic = (row.get("topic") or "").strip()
        url = (row.get("virtualsite_url") or row.get("sourceurl") or row.get("url") or "").strip()
        pdf_url = (row.get("paper_pdf_url") or row.get("pdf_url") or "").strip()
        embed_inputs.append(f"{title}\n{zh_title}\n{abstract}\n{zh_abstract}\n{keywords}")
        payload.append((external_id, title, abstract or zh_abstract, authors, conference.lower(), year, decision, eventtype, topic, keywords, url, pdf_url))
    if not payload:
        raise ValueError("CSV did not contain any rows with a title column.")
    embeddings = embed_texts(embed_inputs)
    update_task("writing_conference", 0, len(payload), "Writing conference papers to SQLite")
    with conn:
        for index, (row, embedding) in enumerate(zip(payload, embeddings), start=1):
            conn.execute(
                """
                INSERT INTO conference_papers(
                    external_id, title, abstract, authors, conference, year, decision, eventtype,
                    topic, keywords, url, pdf_url, embedding, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(external_id, conference, year) DO UPDATE SET
                    title=excluded.title,
                    abstract=excluded.abstract,
                    authors=excluded.authors,
                    decision=excluded.decision,
                    eventtype=excluded.eventtype,
                    topic=excluded.topic,
                    keywords=excluded.keywords,
                    url=excluded.url,
                    pdf_url=excluded.pdf_url,
                    embedding=excluded.embedding,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (*row, dumps_embedding(embedding)),
            )
            if index == len(payload) or index % 200 == 0:
                update_task("writing_conference", index, len(payload), f"Writing conference papers {index}/{len(payload)}")
    return ImportSummary(imported=len(payload), total=len(rows), detail=f"Imported {conference.upper()} {year} papers.")


def rebuild_all_embeddings(conn: sqlite3.Connection) -> ImportSummary:
    zotero_rows = conn.execute(
        "SELECT id, title, abstract, tags FROM zotero_items ORDER BY id"
    ).fetchall()
    paper_rows = conn.execute(
        "SELECT id, title, abstract, keywords, topic FROM conference_papers ORDER BY id"
    ).fetchall()
    zotero_inputs = [
        f"{row['title']}\n{row['abstract']}\n{' '.join(json.loads(row['tags'] or '[]'))}"
        for row in zotero_rows
    ]
    paper_inputs = [
        f"{row['title']}\n{row['abstract']}\n{row['keywords']}\n{row['topic']}"
        for row in paper_rows
    ]
    total = len(zotero_rows) + len(paper_rows)
    update_task("embedding_zotero", 0, total, f"Embedding Zotero items 0/{len(zotero_rows)}")
    zotero_embeddings = embed_texts(zotero_inputs) if zotero_inputs else []
    update_task("embedding_conference", len(zotero_rows), total, f"Embedding conference papers 0/{len(paper_rows)}")
    paper_embeddings = embed_texts(paper_inputs) if paper_inputs else []
    update_task("writing_embeddings", 0, total, "Writing rebuilt embeddings to SQLite")
    with conn:
        written = 0
        for row, embedding in zip(zotero_rows, zotero_embeddings):
            conn.execute(
                "UPDATE zotero_items SET embedding = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (dumps_embedding(embedding), row["id"]),
            )
            written += 1
            if written == total or written % 200 == 0:
                update_task("writing_embeddings", written, total, f"Writing embeddings {written}/{total}")
        for row, embedding in zip(paper_rows, paper_embeddings):
            conn.execute(
                "UPDATE conference_papers SET embedding = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (dumps_embedding(embedding), row["id"]),
            )
            written += 1
            if written == total or written % 200 == 0:
                update_task("writing_embeddings", written, total, f"Writing embeddings {written}/{total}")
    rebuild_interest_profiles(conn)
    return ImportSummary(imported=total, total=total, detail="Rebuilt local embeddings for Zotero items and conference papers.")


def rebuild_interest_profiles(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT tags, embedding FROM zotero_items").fetchall()
    groups: dict[str, list[list[float]]] = {"All Zotero": []}
    keywords: dict[str, set[str]] = {"All Zotero": set()}
    for row in rows:
        embedding = loads_embedding(row["embedding"])
        groups["All Zotero"].append(embedding)
        try:
            tags = json.loads(row["tags"] or "[]")
        except json.JSONDecodeError:
            tags = []
        for tag in tags:
            tag = str(tag).strip()
            if not tag:
                continue
            groups.setdefault(tag, []).append(embedding)
            keywords.setdefault(tag, set()).add(tag)
            keywords["All Zotero"].add(tag)
    with conn:
        conn.execute("DELETE FROM interest_profiles")
        for name, vectors in sorted(groups.items(), key=lambda item: (item[0] != "All Zotero", item[0].lower())):
            centroid = mean_embedding(vectors)
            conn.execute(
                """
                INSERT INTO interest_profiles(name, source_type, keywords, centroid_embedding, item_count, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    name,
                    "zotero_tag" if name != "All Zotero" else "zotero_library",
                    json.dumps(sorted(keywords.get(name, set())), ensure_ascii=False),
                    dumps_embedding(centroid),
                    len(vectors),
                ),
            )
