from __future__ import annotations

import difflib
import html
import json
import re
import sqlite3
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


API_BASE = "https://api.zotero.org"
LOCAL_API_BASE = "http://127.0.0.1:23119/api/users/0"
CONNECTOR_PING_URL = "http://127.0.0.1:23119/connector/ping"
CONNECTOR_BASE = "http://127.0.0.1:23119/connector"
API_VERSION = "3"
BATCH_SIZE = 50
MAX_PDF_BYTES = 80 * 1024 * 1024

ARXIV_API = "http://export.arxiv.org/api/query"
ARXIV_MATCH_THRESHOLD = 0.88
_arxiv_pdf_cache: dict[str, str] = {}


def local_zotero_status() -> dict[str, Any]:
    connector = False
    local_api = False
    message = "Zotero is not reachable."
    try:
        with urllib.request.urlopen(CONNECTOR_PING_URL, timeout=3) as response:
            connector = response.status == 200
            message = "Zotero is running."
    except urllib.error.URLError:
        return {"connector": connector, "local_api": local_api, "message": message}
    try:
        _local_zotero_request("GET", "/collections?limit=1")
        local_api = True
        message = "Zotero Local API is enabled."
    except ValueError as exc:
        message = str(exc)
    return {"connector": connector, "local_api": local_api, "message": message}


def list_local_zotero_collections() -> list[dict[str, Any]]:
    payload = _connector_request("POST", "/getSelectedCollection", {})
    items: list[dict[str, Any]] = []
    # Targets come back in depth-first order with a `level` (0 = library, 1 = top
    # collection, 2+ = nested), so the parent of a level-N row is the most recent
    # row at level N-1.
    ancestor_by_level: dict[int, str] = {}
    for target in payload.get("targets", []) if isinstance(payload, dict) else []:
        target_id = str(target.get("id") or "")
        name = str(target.get("name") or "")
        if not target_id.startswith("C") or not name:
            continue
        level = int(target.get("level") or 1)
        ancestor_by_level[level] = target_id
        parent = ancestor_by_level.get(level - 1, "") if level > 1 else ""
        items.append({"key": target_id, "name": name, "level": level, "parent": parent})
    return items


def list_zotero_collections(api_key: str, library_type: str, library_id: str) -> list[dict[str, str]]:
    path = _library_path(library_type, library_id, "collections?limit=100")
    payload = _zotero_request("GET", path, api_key)
    return _collection_items(payload)


def _collection_items(payload: Any) -> list[dict[str, str]]:
    items = []
    for row in payload if isinstance(payload, list) else []:
        data = row.get("data", {})
        key = data.get("key") or row.get("key")
        name = data.get("name")
        if key and name:
            parent = data.get("parentCollection") or ""
            items.append({"key": key, "name": name, "parent": parent})
    return items


def export_papers_to_local_zotero(
    conn: sqlite3.Connection,
    paper_ids: list[int],
    collection_key: str,
) -> dict[str, Any]:
    return _export_papers(conn, paper_ids, collection_key, _send_connector_items)


def export_papers_to_zotero(
    conn: sqlite3.Connection,
    paper_ids: list[int],
    api_key: str,
    library_type: str,
    library_id: str,
    collection_key: str,
) -> dict[str, Any]:
    endpoint = _library_path(library_type, library_id, "items")
    return _export_papers(conn, paper_ids, collection_key, lambda items, _target: _zotero_request("POST", endpoint, api_key, [_web_item(item) for item in items]))


def _export_papers(
    conn: sqlite3.Connection,
    paper_ids: list[int],
    collection_key: str,
    send_batch,
) -> dict[str, Any]:
    if not paper_ids:
        raise ValueError("No selected papers.")
    if not collection_key.strip():
        raise ValueError("Zotero collection key is required.")
    placeholders = ",".join("?" for _ in paper_ids)
    rows = conn.execute(
        f"SELECT * FROM conference_papers WHERE id IN ({placeholders})",
        paper_ids,
    ).fetchall()
    by_id = {row["id"]: row for row in rows}
    ordered = [by_id[paper_id] for paper_id in paper_ids if paper_id in by_id]
    if not ordered:
        raise ValueError("Selected papers were not found in the local database.")

    successful = 0
    unchanged = 0
    failed: list[dict[str, Any]] = []
    for start in range(0, len(ordered), BATCH_SIZE):
        batch = ordered[start : start + BATCH_SIZE]
        items = [_paper_to_zotero_item(row, collection_key) for row in batch]
        response = send_batch(items, collection_key.strip())
        successful += len(response.get("successful", {})) if isinstance(response, dict) else 0
        unchanged += len(response.get("unchanged", {})) if isinstance(response, dict) else 0
        failed_payload = response.get("failed", {}) if isinstance(response, dict) else {}
        for index, detail in failed_payload.items():
            paper = batch[int(index)] if str(index).isdigit() and int(index) < len(batch) else None
            failed.append({"title": paper["title"] if paper else "", "detail": detail})
    return {
        "ok": len(failed) == 0,
        "requested": len(ordered),
        "successful": successful,
        "unchanged": unchanged,
        "failed": failed,
    }


def _library_path(library_type: str, library_id: str, suffix: str) -> str:
    clean_type = library_type.strip().lower()
    clean_id = library_id.strip()
    if clean_type not in {"users", "groups"}:
        raise ValueError("Zotero library type must be users or groups.")
    if not clean_id:
        raise ValueError("Zotero library ID is required.")
    return f"/{clean_type}/{clean_id}/{suffix}"


def _zotero_request(method: str, path: str, api_key: str, body: Any | None = None) -> Any:
    clean_key = api_key.strip()
    if not clean_key:
        raise ValueError("Zotero API key is required.")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{API_BASE}{path}",
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Zotero-API-Key": clean_key,
            "Zotero-API-Version": API_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Zotero API error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Cannot reach Zotero API: {exc.reason}") from exc
    return json.loads(text) if text else {}


def _local_zotero_request(method: str, path: str, body: Any | None = None) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{LOCAL_API_BASE}{path}",
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Zotero-API-Version": API_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Zotero Local API error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Cannot reach Zotero Local API: {exc.reason}") from exc
    if text.strip() == "Local API is not enabled":
        raise ValueError("Zotero is running, but Local API is not enabled.")
    try:
        return json.loads(text) if text else {}
    except json.JSONDecodeError as exc:
        raise ValueError(text.strip() or "Zotero Local API returned an invalid response.") from exc


def _connector_request(method: str, path: str, body: Any | None = None, headers: dict[str, str] | None = None) -> Any:
    if isinstance(body, bytes):
        data = body
        request_headers = headers or {"Content-Type": "application/octet-stream"}
    else:
        data = json.dumps(body or {}).encode("utf-8")
        request_headers = {"Content-Type": "application/json", **(headers or {})}
    request = urllib.request.Request(
        f"{CONNECTOR_BASE}{path}",
        data=data,
        method=method,
        headers=request_headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Zotero Connector error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Cannot reach Zotero Connector: {exc.reason}") from exc
    return json.loads(text) if text else {}


def _send_connector_items(items: list[dict[str, Any]], target: str) -> dict[str, Any]:
    """Save each paper to Zotero in its own connector session.

    The Zotero Connector's /saveItems fails the *entire* request with HTTP 500 if any
    single item has metadata it dislikes. Saving one item per session isolates such a
    paper so the rest still import, and lets us report exactly which paper failed.
    """
    successful: dict[str, Any] = {}
    failed: dict[str, Any] = {}
    for index, item in enumerate(items):
        connector_item = _paper_item_for_connector(item, index)
        try:
            _save_one_connector_item(connector_item, target)
            successful[str(index)] = True
        except ValueError as exc:
            failed[str(index)] = _explain_connector_error(exc)
            _log(f"FAILED {connector_item.get('title','')!r}: {exc!s}")
    return {"successful": successful, "unchanged": {}, "failed": failed}


def _log(message: str) -> None:
    import sys
    print(f"[zotero-export] {message}", file=sys.stderr, flush=True)


def _save_one_connector_item(connector_item: dict[str, Any], target: str) -> None:
    pdf_url = connector_item.pop("_pdf_url", "")
    title = connector_item.get("title", "")
    session_id = _save_items_with_retry(connector_item, target)
    # updateSession can also hit a transient 500 while Zotero is busy; retry it too.
    _retry_500(
        lambda: _connector_request("POST", "/updateSession", {"sessionID": session_id, "target": target}),
        what=f"updateSession {title!r}",
    )
    if pdf_url:
        _save_pdf_attachment(session_id, connector_item["id"], title, pdf_url)


def _retry_500(call, what: str, attempts: int = 5):
    """Run a connector call, retrying its transient empty-bodied HTTP 500s with backoff."""
    last_exc: ValueError | None = None
    for attempt in range(attempts):
        try:
            return call()
        except ValueError as exc:
            if "error 500" not in str(exc):
                raise
            last_exc = exc
            _log(f"500 on {what} attempt {attempt + 1}/{attempts}: {exc!s}")
            time.sleep(0.5 * (attempt + 1))
    raise last_exc if last_exc else ValueError("Zotero Connector error 500: ")


def _save_items_with_retry(connector_item: dict[str, Any], target: str) -> str:
    """Save one item, tolerating Zotero's transient /saveItems 500s.

    The connector intermittently returns an empty-bodied HTTP 500 when it is busy
    (syncing, indexing attachments, etc.); the same item succeeds moments later. We
    retry with backoff. Only if every attempt fails do we try once without creators,
    in case the metadata itself is genuinely rejected. Each /saveItems needs a fresh
    session, since even a failed call registers its sessionID.
    """
    title = connector_item.get("title", "")
    try:
        return _retry_500(lambda: _post_save_items([connector_item], target), what=f"saveItems {title!r}")
    except ValueError:
        if not connector_item.get("creators"):
            raise
        session = _post_save_items([{**connector_item, "creators": []}], target)
        _log(f"recovered without creators: {title!r}")
        return session


def _post_save_items(items: list[dict[str, Any]], target: str) -> str:
    session_id = f"paper-radar-{uuid.uuid4().hex}"
    _connector_request(
        "POST",
        "/saveItems",
        {
            "items": items,
            "uri": "http://paper-radar.local/selected-papers",
            "sessionID": session_id,
            "target": target,
        },
    )
    return session_id


def _explain_connector_error(exc: Exception) -> str:
    detail = str(exc)
    if "error 500" in detail:
        return "Zotero rejected this paper's metadata (Connector error 500)."
    return detail


def _web_item(item: dict[str, Any]) -> dict[str, Any]:
    clean = dict(item)
    clean.pop("_pdf_url", None)
    clean["creators"] = _clean_creators(clean.get("creators"))
    return clean


def _paper_item_for_connector(item: dict[str, Any], index: int) -> dict[str, Any]:
    connector_item = dict(item)
    connector_item.pop("collections", None)
    connector_item["creators"] = _clean_creators(connector_item.get("creators"))
    connector_item["id"] = connector_item.get("id") or f"paper-radar-item-{index}-{uuid.uuid4().hex[:8]}"
    return connector_item


def _clean_creators(creators: Any) -> list[dict[str, str]]:
    """Drop creators that have no usable name.

    The Zotero Connector returns HTTP 500 on /saveItems when any item contains a
    creator whose name is entirely blank (both first/last empty, an empty single
    `name`, or missing name keys). A single such creator fails the whole batch, so
    we strip them out before sending.
    """
    cleaned = []
    for creator in creators if isinstance(creators, list) else []:
        if not isinstance(creator, dict):
            continue
        has_name = any(str(creator.get(key) or "").strip() for key in ("firstName", "lastName", "name"))
        if has_name:
            cleaned.append(creator)
    return cleaned


def _save_pdf_attachment(session_id: str, parent_item_id: str, title: str, pdf_url: str) -> None:
    request = urllib.request.Request(pdf_url, headers={"User-Agent": "Paper Radar Zotero Export"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            content_type = response.headers.get("Content-Type", "application/pdf").split(";")[0]
            data = response.read(MAX_PDF_BYTES + 1)
    except urllib.error.URLError as exc:
        raise ValueError(f"PDF download failed: {exc.reason}") from exc
    if len(data) > MAX_PDF_BYTES:
        raise ValueError("PDF is larger than the 80 MB local export limit.")
    metadata = {
        "sessionID": session_id,
        "parentItemID": parent_item_id,
        "title": f"{title}.pdf"[:180],
        "url": pdf_url,
    }
    _connector_request(
        "POST",
        "/saveAttachment",
        data,
        headers={
            "Content-Type": content_type or "application/pdf",
            "X-Metadata": json.dumps(metadata),
        },
    )


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _resolve_arxiv_pdf(title: str) -> str:
    """Best-effort PDF lookup for papers without one: find the same paper on arXiv.

    A title-similarity check guards against attaching the wrong PDF. Results are
    cached, and a short politeness delay keeps us within arXiv's rate limits.
    """
    title = (title or "").strip()
    if not title:
        return ""
    if title in _arxiv_pdf_cache:
        return _arxiv_pdf_cache[title]
    result = ""
    try:
        words = re.sub(r"[^\w\s]", " ", title).split()[:10]
        query = urllib.parse.quote(f'ti:"{" ".join(words)}"')
        request = urllib.request.Request(
            f"{ARXIV_API}?search_query={query}&max_results=5",
            headers={"User-Agent": "Paper Radar (arxiv pdf lookup)"},
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            xml = response.read().decode("utf-8", "replace")
        time.sleep(0.34)
        target = _normalize_title(title)
        best_ratio, best_id = 0.0, ""
        for block in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
            title_match = re.search(r"<title>(.*?)</title>", block, re.S)
            id_match = re.search(r"<id>(http://arxiv\.org/abs/[^<]+)</id>", block)
            if not title_match or not id_match:
                continue
            found_title = html.unescape(re.sub(r"\s+", " ", title_match.group(1)).strip())
            ratio = difflib.SequenceMatcher(None, target, _normalize_title(found_title)).ratio()
            if ratio > best_ratio:
                best_ratio, best_id = ratio, id_match.group(1).rsplit("/", 1)[-1]
        if best_ratio >= ARXIV_MATCH_THRESHOLD and best_id:
            result = f"https://arxiv.org/pdf/{best_id}"
    except (urllib.error.URLError, ValueError, OSError):
        result = ""
    _arxiv_pdf_cache[title] = result
    return result


def _paper_to_zotero_item(row: sqlite3.Row, collection_key: str) -> dict[str, Any]:
    conference = str(row["conference"] or "").upper()
    year = str(row["year"] or "")
    url = row["url"] or row["pdf_url"] or ""
    pdf_url = row["pdf_url"] or _resolve_arxiv_pdf(row["title"] or "")
    extra = f"PDF: {pdf_url}" if pdf_url and pdf_url != url else ""
    return {
        "itemType": "conferencePaper",
        "title": row["title"] or "",
        "creators": _parse_creators(row["authors"] or ""),
        "abstractNote": row["abstract"] or "",
        "proceedingsTitle": f"{conference} {year}".strip(),
        "conferenceName": conference,
        "date": year,
        "url": url,
        "extra": extra,
        "collections": [collection_key.strip()],
        "_pdf_url": pdf_url,
    }


def _parse_creators(authors: str) -> list[dict[str, str]]:
    parts = re.split(r"\s*;\s*|\s+\band\b\s+|\s*\|\s*", authors)
    creators = []
    for raw in parts[:25]:
        name = " ".join(raw.replace("\n", " ").split()).strip(" ,")
        if not name:
            continue
        if "," in name:
            last, first = [part.strip() for part in name.split(",", 1)]
        else:
            bits = name.split()
            first, last = (" ".join(bits[:-1]), bits[-1]) if len(bits) > 1 else ("", name)
        if not first.strip() and not last.strip():
            continue
        creators.append({"creatorType": "author", "firstName": first, "lastName": last})
    return creators
