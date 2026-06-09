from __future__ import annotations

import sqlite3

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from .db import connect, init_db, row_to_dict
from .figures import cache_stats, clear_cache, extract_figures, figure_file_path, list_figure_papers
from .importers import discover_zotero_sqlite, import_conference_csv, import_zotero_bibtex, import_zotero_sqlite, rebuild_all_embeddings, rebuild_interest_profiles
from .matcher import export_matches_csv, list_matches, match_custom_text, run_matching, upsert_custom_profile
from .progress import fail_task, finish_task, get_task, start_task
from .zotero_api import export_papers_to_local_zotero, export_papers_to_zotero, list_local_zotero_collections, list_zotero_collections, local_zotero_status


app = FastAPI(title="Paper Radar", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class MatchRunRequest(BaseModel):
    conference: str
    year: int
    limit_per_profile: int = 100


class ZoteroBibtexImportRequest(BaseModel):
    text: str


class ConferenceCsvImportRequest(BaseModel):
    text: str
    conference: str
    year: int


class ZoteroLocalImportRequest(BaseModel):
    path: str | None = None
    collection: str | None = None


class FeedbackRequest(BaseModel):
    paper_id: int
    profile_id: int | None = None
    action: str
    note: str = ""


class CustomProfileRequest(BaseModel):
    name: str
    description: str
    keywords: list[str] | None = None


class CustomMatchRequest(BaseModel):
    text: str
    conference: str | None = None
    year: int | None = None
    limit: int = 50


class ZoteroCollectionsRequest(BaseModel):
    api_key: str
    library_type: str = "users"
    library_id: str


class ZoteroExportRequest(BaseModel):
    paper_ids: list[int]
    api_key: str
    library_type: str = "users"
    library_id: str
    collection_key: str


class ZoteroLocalExportRequest(BaseModel):
    paper_ids: list[int]
    collection_key: str


def _raise_db_error(exc: sqlite3.OperationalError) -> None:
    message = str(exc)
    if "locked" in message.lower():
        detail = "SQLite database is busy. Stop other Paper Radar tasks or wait a moment, then retry."
        raise HTTPException(status_code=503, detail=detail) from exc
    raise HTTPException(status_code=500, detail=message) from exc


@app.on_event("startup")
def startup() -> None:
    with connect() as conn:
        init_db(conn)


@app.get("/api/health")
def health() -> dict[str, object]:
    with connect() as conn:
        init_db(conn)
        zotero = conn.execute("SELECT COUNT(*) AS count FROM zotero_items").fetchone()["count"]
        zotero_abstracts = conn.execute(
            "SELECT COUNT(*) AS count FROM zotero_items WHERE LENGTH(TRIM(abstract)) > 0"
        ).fetchone()["count"]
        papers = conn.execute("SELECT COUNT(*) AS count FROM conference_papers").fetchone()["count"]
        profiles = conn.execute("SELECT COUNT(*) AS count FROM interest_profiles").fetchone()["count"]
        runs = conn.execute("SELECT COUNT(*) AS count FROM match_runs").fetchone()["count"]
        conferences = [
            dict(row)
            for row in conn.execute(
                """
                SELECT conference, year, COUNT(*) AS count
                FROM conference_papers
                GROUP BY conference, year
                ORDER BY year DESC, conference ASC
                """
            ).fetchall()
        ]
    return {
        "ok": True,
        "zotero_items": zotero,
        "zotero_abstracts": zotero_abstracts,
        "conference_papers": papers,
        "profiles": profiles,
        "match_runs": runs,
        "conferences": conferences,
    }


@app.get("/api/tasks/current")
def current_task() -> dict[str, object]:
    return get_task()


@app.post("/api/import/zotero-bibtex")
def import_zotero(request: ZoteroBibtexImportRequest) -> dict[str, object]:
    try:
        start_task("import_zotero_bibtex", message="Importing Zotero BibTeX")
        with connect() as conn:
            init_db(conn)
            summary = import_zotero_bibtex(conn, request.text)
        finish_task(summary.detail)
        return {"ok": True, **summary.__dict__}
    except ValueError as exc:
        fail_task(str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.OperationalError as exc:
        fail_task(str(exc))
        _raise_db_error(exc)


@app.get("/api/zotero/discover")
def discover_zotero() -> dict[str, object]:
    path = discover_zotero_sqlite()
    return {"found": path is not None, "path": str(path) if path else ""}


@app.post("/api/import/zotero-local")
def import_zotero_local(request: ZoteroLocalImportRequest) -> dict[str, object]:
    try:
        start_task("import_zotero_local", message="Reading local Zotero database")
        with connect() as conn:
            init_db(conn)
            summary = import_zotero_sqlite(conn, request.path, request.collection)
        finish_task(summary.detail)
        return {"ok": True, **summary.__dict__}
    except ValueError as exc:
        fail_task(str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.OperationalError as exc:
        fail_task(str(exc))
        _raise_db_error(exc)


@app.post("/api/zotero/collections")
def zotero_collections(request: ZoteroCollectionsRequest) -> dict[str, object]:
    try:
        items = list_zotero_collections(request.api_key, request.library_type, request.library_id)
        return {"items": items}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/zotero/local/status")
def zotero_local_status() -> dict[str, object]:
    return local_zotero_status()


@app.get("/api/zotero/local/collections")
def zotero_local_collections() -> dict[str, object]:
    try:
        items = list_local_zotero_collections()
        return {"items": items}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/export/zotero")
def export_zotero(request: ZoteroExportRequest) -> dict[str, object]:
    try:
        with connect() as conn:
            init_db(conn)
            result = export_papers_to_zotero(
                conn,
                request.paper_ids,
                request.api_key,
                request.library_type,
                request.library_id,
                request.collection_key,
            )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.OperationalError as exc:
        _raise_db_error(exc)


@app.post("/api/export/zotero-local")
def export_zotero_local(request: ZoteroLocalExportRequest) -> dict[str, object]:
    try:
        with connect() as conn:
            init_db(conn)
            result = export_papers_to_local_zotero(conn, request.paper_ids, request.collection_key)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.OperationalError as exc:
        _raise_db_error(exc)


@app.post("/api/import/conference-csv")
def import_conference(request: ConferenceCsvImportRequest) -> dict[str, object]:
    try:
        start_task("import_conference_csv", message=f"Importing {request.conference.upper()} {request.year} CSV")
        with connect() as conn:
            init_db(conn)
            summary = import_conference_csv(conn, request.text, request.conference, request.year)
        finish_task(summary.detail)
        return {"ok": True, **summary.__dict__}
    except ValueError as exc:
        fail_task(str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.OperationalError as exc:
        fail_task(str(exc))
        _raise_db_error(exc)


@app.post("/api/matches/run")
def run_matches(request: MatchRunRequest) -> dict[str, object]:
    try:
        start_task("run_matching", message=f"Ranking {request.conference.upper()} {request.year}")
        with connect() as conn:
            init_db(conn)
            result = run_matching(conn, request.conference, request.year, request.limit_per_profile)
        finish_task(f"Ranking complete: {result['results']} results")
        return {"ok": True, **result}
    except ValueError as exc:
        fail_task(str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.OperationalError as exc:
        fail_task(str(exc))
        _raise_db_error(exc)


@app.get("/api/profiles")
def profiles() -> dict[str, object]:
    with connect() as conn:
        init_db(conn)
        rows = conn.execute("SELECT * FROM interest_profiles ORDER BY name").fetchall()
    items = []
    for row in rows:
        item = row_to_dict(row)
        if item:
            item["quality"] = _profile_quality(item)
            item["source_label"] = _profile_source_label(item)
            items.append(item)
    summary: dict[str, int] = {}
    for item in items:
        summary[item["quality"]] = summary.get(item["quality"], 0) + 1
    return {"items": items, "summary": summary}


def _profile_source_label(item: dict[str, object]) -> str:
    source_type = str(item.get("source_type") or "")
    if source_type == "zotero_library":
        return "Zotero 全库"
    if source_type == "custom_text":
        return "自定义方向"
    if source_type == "zotero_tag":
        return "Zotero 标签 / Collection"
    return source_type or "未知来源"


def _profile_quality(item: dict[str, object]) -> str:
    name = str(item.get("name") or "")
    source_type = str(item.get("source_type") or "")
    item_count = int(item.get("item_count") or 0)
    lower = name.lower()
    noisy_prefixes = (
        "computer science",
        "electrical engineering",
        "statistics -",
        "quantitative biology",
        "machine learning (",
        "computation and language",
        "multimedia (",
        "artificial intelligence (",
        "computer vision and pattern recognition",
        "fos:",
    )
    noisy_exact = {"image", "text", "training", "reviews", "surveys", "visualization", "i.2.7", "68t50", "⭐", "学长分享", "重要todo"}
    if source_type == "zotero_library":
        return "library"
    if source_type == "custom_text":
        return "custom"
    if lower in noisy_exact or lower.startswith(noisy_prefixes) or item_count <= 1:
        return "noisy"
    return "curated"


@app.post("/api/profiles/rebuild")
def rebuild_profiles() -> dict[str, object]:
    with connect() as conn:
        init_db(conn)
        rebuild_interest_profiles(conn)
        count = conn.execute("SELECT COUNT(*) AS count FROM interest_profiles").fetchone()["count"]
    return {"ok": True, "profiles": count}


@app.post("/api/profiles/custom")
def custom_profile(request: CustomProfileRequest) -> dict[str, object]:
    try:
        with connect() as conn:
            init_db(conn)
            profile = upsert_custom_profile(conn, request.name, request.description, request.keywords)
        return {"ok": True, "profile": profile}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.OperationalError as exc:
        _raise_db_error(exc)


@app.delete("/api/profiles/{profile_id}")
def delete_profile(profile_id: int) -> dict[str, object]:
    try:
        with connect() as conn:
            init_db(conn)
            profile = conn.execute("SELECT id, name, source_type FROM interest_profiles WHERE id = ?", (profile_id,)).fetchone()
            if not profile:
                raise HTTPException(status_code=404, detail="Profile not found.")
            if profile["source_type"] != "custom_text":
                raise HTTPException(status_code=400, detail="Only custom profiles can be deleted.")
            with conn:
                conn.execute("DELETE FROM interest_profiles WHERE id = ?", (profile_id,))
        return {"ok": True, "deleted": profile_id}
    except sqlite3.OperationalError as exc:
        _raise_db_error(exc)


@app.post("/api/embeddings/rebuild")
def rebuild_embeddings() -> dict[str, object]:
    try:
        start_task("rebuild_embeddings", message="Rebuilding local embeddings")
        with connect() as conn:
            init_db(conn)
            summary = rebuild_all_embeddings(conn)
        finish_task(summary.detail)
        return {"ok": True, **summary.__dict__}
    except sqlite3.OperationalError as exc:
        fail_task(str(exc))
        _raise_db_error(exc)


@app.post("/api/matches/custom")
def custom_matches(request: CustomMatchRequest) -> dict[str, object]:
    try:
        with connect() as conn:
            init_db(conn)
            items = match_custom_text(conn, request.text, request.conference, request.year, request.limit)
        return {"items": items}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.OperationalError as exc:
        _raise_db_error(exc)


@app.get("/api/matches")
def matches(
    profile_id: int | None = None,
    conference: str | None = None,
    year: int | None = None,
    limit: int = 100,
    action: str | None = None,
) -> dict[str, object]:
    if action and action not in {"relevant", "not_relevant", "want_to_read", "read", "hide"}:
        raise HTTPException(status_code=400, detail="Invalid feedback action.")
    with connect() as conn:
        init_db(conn)
        items = list_matches(conn, profile_id, conference, year, max(1, min(limit, 1000)), action)
    return {"items": items}


@app.get("/api/papers/{paper_id}")
def paper_detail(paper_id: int) -> dict[str, object]:
    with connect() as conn:
        init_db(conn)
        paper = conn.execute("SELECT * FROM conference_papers WHERE id = ?", (paper_id,)).fetchone()
        if not paper:
            raise HTTPException(status_code=404, detail="Paper not found.")
        feedback = conn.execute("SELECT * FROM feedback WHERE paper_id = ? ORDER BY created_at DESC", (paper_id,)).fetchall()
    return {"paper": row_to_dict(paper), "feedback": [row_to_dict(row) for row in feedback]}


@app.post("/api/feedback")
def feedback(request: FeedbackRequest) -> dict[str, object]:
    if request.action not in {"relevant", "not_relevant", "want_to_read", "read", "hide"}:
        raise HTTPException(status_code=400, detail="Invalid feedback action.")
    with connect() as conn:
        init_db(conn)
        with conn:
            conn.execute(
                "INSERT INTO feedback(paper_id, profile_id, action, note) VALUES (?, ?, ?, ?)",
                (request.paper_id, request.profile_id, request.action, request.note),
            )
    return {"ok": True}


@app.get("/api/export/matches.csv")
def export_matches() -> Response:
    with connect() as conn:
        init_db(conn)
        csv_text = export_matches_csv(conn)
    return Response(
        csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=paper_radar_matches.csv"},
    )


@app.get("/api/figures/papers")
def figures_papers(conference: str, year: int, kind: str = "oral", limit: int = 200) -> dict[str, object]:
    if kind not in {"oral", "spotlight", "highlight", "poster", "other", "all"}:
        raise HTTPException(status_code=400, detail="Invalid kind.")
    with connect() as conn:
        init_db(conn)
        return list_figure_papers(conn, conference, year, kind, max(1, min(limit, 1000)))


@app.get("/api/figures/paper/{paper_id}")
def figures_paper(paper_id: int, force: bool = False, persist: bool = False) -> dict[str, object]:
    with connect() as conn:
        init_db(conn)
        return extract_figures(conn, paper_id, force=force, persist=persist)


@app.get("/api/figures/file/{paper_id}/{name}")
def figures_file(paper_id: int, name: str) -> FileResponse:
    path = figure_file_path(paper_id, name)
    if not path:
        raise HTTPException(status_code=404, detail="Figure not found.")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/figures/cache")
def figures_cache() -> dict[str, object]:
    return cache_stats()


@app.delete("/api/figures/cache")
def figures_cache_clear() -> dict[str, object]:
    return clear_cache()
