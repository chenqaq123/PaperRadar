"""On-demand figure/table extraction from conference paper PDFs.

Nothing is pre-crawled. The extractor can either persist crops under
``data/figure_cache/<paper_id>/`` for compatibility, or return inline images
from a temporary directory so the inspiration wall leaves no local cache.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import sqlite3
import tempfile
import urllib.request
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

from .db import get_db_path

# Tunables ------------------------------------------------------------------
MIN_WIDTH = 200          # px, drop icons / rule lines
MIN_HEIGHT = 150
MIN_AREA = 60_000        # px^2
MAX_ASPECT = 7.0         # drop banners / separators
MIN_ASPECT = 1 / 7.0
MAX_SAVE_DIM = 1500      # downscale very large images to save disk
JPEG_QUALITY = 85
MAX_FIGURES = 40
DOWNLOAD_TIMEOUT = 45
EXTRACTOR_VERSION = 7
LAYOUT_RENDER_SCALE = 2.2
_UA = "Paper Radar Figure Viewer"
_CAPTION_RE = re.compile(
    r"^\s*(fig(?:ure)?\.?|table)\s*(?:[sS]?\d+[A-Za-z]?(?:\.\d+)?)(?:\s|[:.|\-–—)]|$)",
    re.IGNORECASE,
)
_TABLE_RE = re.compile(
    r"^\s*table\s*(?:[sS]?\d+[A-Za-z]?(?:\.\d+)?)(?:\s|[:.|\-–—)]|$)",
    re.IGNORECASE,
)


def _cache_root() -> Path:
    root = get_db_path().parent / "figure_cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def classify_paper(decision: str, eventtype: str) -> str:
    text = f"{decision or ''} {eventtype or ''}".lower()
    if "oral" in text:
        return "oral"
    if "spotlight" in text:
        return "spotlight"
    if "highlight" in text:
        return "highlight"
    if "poster" in text:
        return "poster"
    return "other"


def _title_key(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").strip().casefold())


def _duplicate_rank(row: sqlite3.Row) -> tuple[int, int, int]:
    text = f"{row['eventtype'] or ''} {row['url'] or ''}".lower()
    # Prefer the canonical presentation page when CVPR stores both `/oral/`
    # and `/poster/` virtual entries for the same accepted paper.
    oral_rank = 0 if "oral" in text else 1
    pdf_rank = 0 if _normalize_pdf_url(row["pdf_url"]) else 1
    return (oral_rank, pdf_rank, row["id"])


def _dedupe_paper_rows(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    by_title: dict[str, sqlite3.Row] = {}
    order: list[str] = []
    for row in rows:
        key = _title_key(row["title"])
        if not key:
            key = f"id:{row['id']}"
        existing = by_title.get(key)
        if existing is None:
            by_title[key] = row
            order.append(key)
            continue
        if _duplicate_rank(row) < _duplicate_rank(existing):
            by_title[key] = row
    return [by_title[key] for key in order]


def list_figure_papers(
    conn: sqlite3.Connection,
    conference: str,
    year: int,
    kind: str = "oral",
    limit: int = 200,
) -> dict[str, object]:
    rows = conn.execute(
        """
        SELECT id, title, authors, decision, eventtype, url, pdf_url
        FROM conference_papers
        WHERE conference = ? AND year = ?
        ORDER BY title COLLATE NOCASE
        """,
        (conference.lower(), year),
    ).fetchall()
    rows = _dedupe_paper_rows(rows)

    # Full pass for accurate per-kind counts (independent of the item limit).
    counts: dict[str, int] = {}
    labels: list[str] = []
    for row in rows:
        label = classify_paper(row["decision"], row["eventtype"])
        labels.append(label)
        counts[label] = counts.get(label, 0) + 1
    counts["all"] = len(rows)

    items: list[dict[str, object]] = []
    for row, label in zip(rows, labels):
        if kind != "all" and label != kind:
            continue
        items.append(
            {
                "paper_id": row["id"],
                "title": row["title"],
                "authors": row["authors"],
                "kind": label,
                "decision": row["decision"],
                "eventtype": row["eventtype"],
                "url": row["url"],
                "has_pdf": bool(_normalize_pdf_url(row["pdf_url"]) or row["url"]),
                "cached": _meta_path(row["id"]).exists(),
            }
        )
        if len(items) >= limit:
            break
    return {"items": items, "counts": counts}


def _paper_dir(paper_id: int) -> Path:
    return _cache_root() / str(paper_id)


def _meta_path(paper_id: int) -> Path:
    return _paper_dir(paper_id) / "figures.json"


def _normalize_pdf_url(url: str) -> str:
    """Map known abstract-page URLs to their actual PDF.

    CVF (openaccess.thecvf.com) stores either `/papers/X.pdf` or the
    `/html/X.html` landing page depending on the import year. Convert the
    latter to the PDF. OpenReview `/forum?id=` -> `/pdf?id=`.
    """
    url = (url or "").strip()
    if not url:
        return ""
    if "openaccess.thecvf.com" in url and "/html/" in url and url.endswith(".html"):
        return url.replace("/html/", "/papers/")[:-5] + ".pdf"
    if "openreview.net/forum?id=" in url:
        return url.replace("/forum?id=", "/pdf?id=")
    return url


def _resolve_pdf_url(conn: sqlite3.Connection, paper: sqlite3.Row) -> str:
    candidate = _normalize_pdf_url(paper["pdf_url"])
    if candidate:
        return candidate
    # Some imports keep the PDF link only in `url`.
    candidate = _normalize_pdf_url(paper["url"])
    if candidate.endswith(".pdf") or "/pdf?" in candidate or "arxiv.org/pdf" in candidate:
        return candidate
    # Fall back to arXiv lookup (reuse the resolver used for Zotero export).
    try:
        from .zotero_api import _resolve_arxiv_pdf

        return _resolve_arxiv_pdf(paper["title"] or "")
    except Exception:
        return ""


def _looks_like_pdf(data: bytes) -> bool:
    return bool(data) and (data[:4] == b"%PDF" or b"%PDF" in data[:1024])


def _download_pdf(url: str) -> bytes:
    import ssl
    import time

    context = ssl.create_default_context()
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT, context=context) as response:
                data = response.read()
            if not _looks_like_pdf(data):
                raise ValueError("返回的不是 PDF 内容")
            return data
        except Exception as error:  # noqa: BLE001
            last_error = error
            time.sleep(0.6 * (attempt + 1))
    # Fallback: some environments (proxies/TLS) block urllib but allow curl.
    try:
        data = _download_with_curl(url)
        if _looks_like_pdf(data):
            return data
    except Exception:  # noqa: BLE001
        pass
    raise last_error or RuntimeError("download failed")


def _download_with_curl(url: str) -> bytes:
    import shutil
    import subprocess

    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("curl not available")
    result = subprocess.run(
        [curl, "-sSL", "--max-time", str(DOWNLOAD_TIMEOUT), "-A", _UA, url],
        capture_output=True,
        timeout=DOWNLOAD_TIMEOUT + 10,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "ignore")[:200] or "curl failed")
    return result.stdout


def _save_image(raw: bytes, dest: Path) -> tuple[int, int] | None:
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception:
        return None
    if image.mode in ("CMYK", "P", "LA", "RGBA"):
        image = image.convert("RGB")
    elif image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    width, height = image.size
    longest = max(width, height)
    if longest > MAX_SAVE_DIM:
        scale = MAX_SAVE_DIM / longest
        image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))))
    image.save(dest, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return image.size


def _passes_filter(width: int, height: int) -> bool:
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        return False
    if width * height < MIN_AREA:
        return False
    aspect = width / height if height else 99
    return MIN_ASPECT <= aspect <= MAX_ASPECT


def extract_figures(
    conn: sqlite3.Connection,
    paper_id: int,
    force: bool = False,
    persist: bool = False,
) -> dict[str, object]:
    paper = conn.execute(
        "SELECT id, title, authors, conference, year, decision, eventtype, url, pdf_url "
        "FROM conference_papers WHERE id = ?",
        (paper_id,),
    ).fetchone()
    if not paper:
        return {"ok": False, "error": "not_found", "detail": "找不到这篇论文。"}

    meta_path = _meta_path(paper_id)
    if persist and meta_path.exists() and not force:
        try:
            cached = json.loads(meta_path.read_text("utf-8"))
            if cached.get("extractor_version") == EXTRACTOR_VERSION:
                cached["cached"] = True
                cached["persisted"] = True
                return cached
        except Exception:
            pass  # corrupt cache -> re-extract

    pdf_url = _resolve_pdf_url(conn, paper)
    base = {
        "ok": True,
        "paper_id": paper_id,
        "title": paper["title"],
        "authors": paper["authors"],
        "conference": paper["conference"],
        "year": paper["year"],
        "kind": classify_paper(paper["decision"], paper["eventtype"]),
        "url": paper["url"],
        "pdf_url": pdf_url,
        "cached": False,
        "persisted": persist,
        "extractor_version": EXTRACTOR_VERSION,
    }
    if not pdf_url:
        base.update(ok=False, error="no_pdf", detail="这篇论文没有可用的 PDF 链接（也没在 arXiv 找到匹配）。", figures=[])
        return base

    try:
        pdf_bytes = _download_pdf(pdf_url)
    except Exception as error:  # noqa: BLE001
        base.update(ok=False, error="download_failed", detail=f"下载 PDF 失败：{error}", figures=[])
        return base

    if persist:
        paper_dir = _paper_dir(paper_id)
        paper_dir.mkdir(parents=True, exist_ok=True)
        # Clear stale images on force re-extract.
        for old in paper_dir.glob("fig_*.jpg"):
            old.unlink(missing_ok=True)
        return _extract_figures_to_dir(pdf_bytes, paper_dir, base, meta_path)

    with tempfile.TemporaryDirectory(prefix=f"paper-radar-figures-{paper_id}-") as tmp:
        paper_dir = Path(tmp)
        result = _extract_figures_to_dir(pdf_bytes, paper_dir, base, None)
        result["figures"] = _inline_figure_images(result.get("figures", []), paper_dir)
        result["cached"] = False
        result["persisted"] = False
        return result


def _extract_figures_to_dir(
    pdf_bytes: bytes,
    paper_dir: Path,
    base: dict[str, object],
    meta_path: Path | None,
) -> dict[str, object]:
    paper_dir.mkdir(parents=True, exist_ok=True)

    figures: list[dict[str, object]] = []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as error:  # noqa: BLE001
        base.update(ok=False, error="parse_failed", detail=f"解析 PDF 失败：{error}", figures=[])
        return base

    try:
        figures = _extract_layout_figures(doc, paper_dir)
        if not figures:
            figures = _extract_embedded_images(doc, paper_dir)
    finally:
        doc.close()

    base["figures"] = figures
    if not figures:
        base["detail"] = "这篇 PDF 里没有抽到合适的图表区域。"
    if meta_path is not None:
        meta_path.write_text(json.dumps(base, ensure_ascii=False), "utf-8")
    return base


def _inline_figure_images(figures: object, paper_dir: Path) -> list[dict[str, object]]:
    inline: list[dict[str, object]] = []
    if not isinstance(figures, list):
        return inline
    for figure in figures:
        if not isinstance(figure, dict):
            continue
        item = dict(figure)
        name = str(item.get("name", ""))
        path = paper_dir / name
        try:
            data = path.read_bytes()
        except OSError:
            inline.append(item)
            continue
        item["data_url"] = "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
        inline.append(item)
    return inline


def _block_text(block: dict) -> str:
    parts: list[str] = []
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            text = span.get("text", "")
            if text:
                parts.append(text)
    return " ".join(" ".join(parts).split())


def _image_rects(page: fitz.Page, blocks: list[dict]) -> list[fitz.Rect]:
    rects: list[fitz.Rect] = []
    for block in blocks:
        if block.get("type") == 1:
            rect = fitz.Rect(block.get("bbox", (0, 0, 0, 0)))
            if rect.width >= 8 and rect.height >= 8:
                rects.append(rect)
    if rects:
        return rects
    try:
        for image in page.get_image_info(xrefs=True):
            rect = fitz.Rect(image.get("bbox", (0, 0, 0, 0)))
            if rect.width >= 8 and rect.height >= 8:
                rects.append(rect)
    except Exception:
        pass
    return rects


def _caption_blocks(blocks: list[dict]) -> list[tuple[fitz.Rect, str, str]]:
    captions: list[tuple[fitz.Rect, str, str]] = []
    for block in blocks:
        if block.get("type") != 0:
            continue
        text = _block_text(block)
        caption_prefix = text[:120]
        if not text or not _CAPTION_RE.search(caption_prefix):
            continue
        kind = "table" if _TABLE_RE.search(caption_prefix) else "figure"
        captions.append((fitz.Rect(block.get("bbox", (0, 0, 0, 0))), text, kind))
    return sorted(captions, key=lambda item: (item[0].y0, item[0].x0))


def _rect_union(rects: list[fitz.Rect]) -> fitz.Rect:
    rect = fitz.Rect(rects[0])
    for item in rects[1:]:
        rect |= item
    return rect


def _clip_to_page(rect: fitz.Rect, page_rect: fitz.Rect) -> fitz.Rect:
    rect = fitz.Rect(rect)
    rect.x0 = max(page_rect.x0, rect.x0)
    rect.y0 = max(page_rect.y0, rect.y0)
    rect.x1 = min(page_rect.x1, rect.x1)
    rect.y1 = min(page_rect.y1, rect.y1)
    return rect


def _iou(a: fitz.Rect, b: fitz.Rect) -> float:
    inter = fitz.Rect(max(a.x0, b.x0), max(a.y0, b.y0), min(a.x1, b.x1), min(a.y1, b.y1))
    if inter.is_empty or inter.width <= 0 or inter.height <= 0:
        return 0.0
    intersection = inter.width * inter.height
    union = a.width * a.height + b.width * b.height - intersection
    return intersection / union if union > 0 else 0.0


def _overlaps_horizontally(a: fitz.Rect, b: fitz.Rect, margin: float = 40) -> bool:
    return a.x1 >= b.x0 - margin and a.x0 <= b.x1 + margin


def _drawing_rects(page: fitz.Page) -> list[fitz.Rect]:
    rects: list[fitz.Rect] = []
    try:
        drawings = page.get_drawings()
    except Exception:
        return rects
    for drawing in drawings:
        rect = fitz.Rect(drawing.get("rect", (0, 0, 0, 0)))
        if rect.width <= 0 or rect.height <= 0:
            continue
        if rect.width < 6 and rect.height < 6:
            continue
        rects.append(rect)
    return rects


def _save_page_crop(page: fitz.Page, clip: fitz.Rect, dest: Path) -> tuple[int, int] | None:
    if clip.width < 120 or clip.height < 80:
        return None
    matrix = fitz.Matrix(LAYOUT_RENDER_SCALE, LAYOUT_RENDER_SCALE)
    pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
    if pix.width * pix.height < MIN_AREA:
        return None
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    longest = max(image.size)
    if longest > MAX_SAVE_DIM:
        scale = MAX_SAVE_DIM / longest
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))))
    image.save(dest, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return image.size


def _neighbor_content_crop(
    page: fitz.Page,
    caption: fitz.Rect,
    previous_caption: fitz.Rect | None,
    next_caption: fitz.Rect | None,
    content_rects: list[fitz.Rect],
    *,
    prefer_above: bool,
    horizontal_margin: float,
    above_window: float,
    below_window: float,
    max_gap: float,
) -> fitz.Rect | None:
    page_rect = page.rect
    lower = previous_caption.y1 + 6 if previous_caption else page_rect.y0
    upper = next_caption.y0 - 6 if next_caption else page_rect.y1
    usable = [
        rect for rect in content_rects
        if _iou(rect, caption) < 0.05 and rect.width >= 6 and rect.height >= 1.5
    ]
    above = [
        rect for rect in usable
        if rect.y1 <= caption.y0 + 2
        and rect.y0 >= lower
        and caption.y0 - rect.y1 <= page_rect.height * above_window
        and _overlaps_horizontally(rect, caption, margin=horizontal_margin)
    ]
    below = [
        rect for rect in usable
        if rect.y0 >= caption.y1 - 2
        and rect.y1 <= upper
        and rect.y0 - caption.y1 <= page_rect.height * below_window
        and _overlaps_horizontally(rect, caption, margin=horizontal_margin)
    ]
    selected_above = _collect_contiguous_rects(above, caption.y0, "above", max_gap=max_gap)
    selected_below = _collect_contiguous_rects(below, caption.y1, "below", max_gap=max_gap)
    selected = selected_above if prefer_above else selected_below
    if not selected:
        selected = selected_below if prefer_above else selected_above
    if not selected:
        return None
    full = _rect_union([caption, *_merge_nearby_label_rects(selected, usable)])
    clip = _clip_to_page(fitz.Rect(full.x0 - 12, full.y0 - 8, full.x1 + 12, full.y1 + 10), page_rect)
    if clip.width < 120 or clip.height < 70 or clip.width * clip.height < 7_000:
        return None
    return clip


def _merge_nearby_label_rects(seed_rects: list[fitz.Rect], candidates: list[fitz.Rect]) -> list[fitz.Rect]:
    union = _rect_union(seed_rects)
    expanded = fitz.Rect(union.x0 - 18, union.y0 - 22, union.x1 + 18, union.y1 + 22)
    related = [
        rect for rect in candidates
        if rect.x1 >= expanded.x0
        and rect.x0 <= expanded.x1
        and rect.y1 >= expanded.y0
        and rect.y0 <= expanded.y1
    ]
    return related or seed_rects


def _figure_crop_for_caption(
    page: fitz.Page,
    caption: fitz.Rect,
    previous_caption: fitz.Rect | None,
    next_caption: fitz.Rect | None,
    image_rects: list[fitz.Rect],
    text_rects: list[fitz.Rect],
    drawing_rects: list[fitz.Rect],
) -> fitz.Rect | None:
    page_rect = page.rect
    lower = previous_caption.y1 + 8 if previous_caption else page_rect.y0
    upper = next_caption.y0 - 8 if next_caption else page_rect.y1

    above = [
        rect for rect in image_rects
        if rect.y1 <= caption.y0 + 4 and rect.y0 >= lower and (caption.y0 - rect.y1) <= page_rect.height * 0.82
    ]
    below = [
        rect for rect in image_rects
        if rect.y0 >= caption.y1 - 4 and rect.y1 <= upper and (rect.y0 - caption.y1) <= page_rect.height * 0.55
    ]
    if above:
        image_union = _rect_union(above)
        y0 = image_union.y0 - 14
        y1 = caption.y1 + 8
    elif below:
        image_union = _rect_union(below)
        y0 = caption.y0 - 8
        y1 = image_union.y1 + 14
    else:
        # Vector diagrams and algorithm/architecture figures often contain no
        # raster image block in the PDF. Fall back to nearby drawing commands
        # plus their text labels before giving up.
        visual_rects = [rect for rect in drawing_rects if rect.width * rect.height >= 20]
        if visual_rects:
            clip = _neighbor_content_crop(
                page,
                caption,
                previous_caption,
                next_caption,
                [*visual_rects, *text_rects],
                prefer_above=True,
                horizontal_margin=220,
                above_window=0.78,
                below_window=0.55,
                max_gap=44,
            )
            if clip:
                return clip
        return None

    x0 = min(image_union.x0, caption.x0) - 12
    x1 = max(image_union.x1, caption.x1) + 12
    related_text = [
        rect for rect in text_rects
        if rect.y0 >= y0 - 24
        and rect.y1 <= y1 + 24
        and rect.x1 >= x0 - 24
        and rect.x0 <= x1 + 24
    ]
    if related_text:
        full = _rect_union([image_union, caption, *related_text])
        x0 = min(x0, full.x0 - 8)
        y0 = min(y0, full.y0 - 6)
        x1 = max(x1, full.x1 + 8)
        y1 = max(y1, full.y1 + 6)
    clip = _clip_to_page(fitz.Rect(x0, y0, x1, y1), page_rect)
    if clip.width * clip.height < 5_000:
        return None
    return clip


def _nearby_table_rects(
    page: fitz.Page,
    caption: fitz.Rect,
    previous_caption: fitz.Rect | None,
    next_caption: fitz.Rect | None,
) -> list[fitz.Rect]:
    try:
        tables = page.find_tables()
    except Exception:
        return []
    page_rect = page.rect
    lower = previous_caption.y1 + 6 if previous_caption else page_rect.y0
    upper = next_caption.y0 - 6 if next_caption else page_rect.y1
    best_above: tuple[float, fitz.Rect] | None = None
    best_below: tuple[float, fitz.Rect] | None = None
    best_overlap: tuple[float, fitz.Rect] | None = None
    for table in getattr(tables, "tables", []):
        try:
            rect = fitz.Rect(table.bbox)
        except Exception:
            continue
        if rect.is_empty or rect.width <= 20 or rect.height <= 12:
            continue
        gap_above = caption.y0 - rect.y1
        gap_below = rect.y0 - caption.y1
        if not _overlaps_horizontally(rect, caption, margin=120):
            continue
        x_score = abs((rect.x0 + rect.x1) - (caption.x0 + caption.x1)) * 0.02
        if gap_above >= -2 and rect.y0 >= lower and gap_above <= page_rect.height * 0.16:
            score = max(0, gap_above) + x_score
            if best_above is None or score < best_above[0]:
                best_above = (score, rect)
        elif gap_below >= -2 and rect.y1 <= upper and gap_below <= page_rect.height * 0.14:
            score = max(0, gap_below) + x_score + 18
            if best_below is None or score < best_below[0]:
                best_below = (score, rect)
        elif rect.y0 >= lower and rect.y1 <= upper:
            score = min(abs(gap_above), abs(gap_below)) + x_score + 28
            if best_overlap is None or score < best_overlap[0]:
                best_overlap = (score, rect)
    best = best_above or best_below or best_overlap
    return [best[1]] if best else []


def _collect_contiguous_rects(
    candidates: list[fitz.Rect],
    anchor_y: float,
    direction: str,
    max_gap: float = 28,
) -> list[fitz.Rect]:
    if direction == "below":
        ordered = sorted(candidates, key=lambda rect: (rect.y0, rect.x0))
        last_y = anchor_y
    else:
        ordered = sorted(candidates, key=lambda rect: (-rect.y1, rect.x0))
        last_y = anchor_y
    selected: list[fitz.Rect] = []
    for rect in ordered:
        gap = rect.y0 - last_y if direction == "below" else last_y - rect.y1
        if selected and gap > max_gap:
            break
        if gap > max_gap * 1.7:
            continue
        selected.append(rect)
        last_y = max(last_y, rect.y1) if direction == "below" else min(last_y, rect.y0)
    return selected


def _table_crop_for_caption(
    page: fitz.Page,
    caption: fitz.Rect,
    previous_caption: fitz.Rect | None,
    next_caption: fitz.Rect | None,
    text_rects: list[fitz.Rect],
    drawing_rects: list[fitz.Rect],
    other_caption_rects: list[fitz.Rect],
) -> fitz.Rect | None:
    page_rect = page.rect
    lower = previous_caption.y1 + 6 if previous_caption else page_rect.y0
    upper = next_caption.y0 - 6 if next_caption else page_rect.y1

    detected = _nearby_table_rects(page, caption, previous_caption, next_caption)
    if detected:
        table_union = _rect_union(detected)
        expanded = _expand_detected_table(
            page,
            caption,
            previous_caption,
            next_caption,
            table_union,
            text_rects,
            drawing_rects,
            other_caption_rects,
        )
        full = _rect_union([caption, expanded or table_union])
        return _clip_to_page(fitz.Rect(full.x0 - 10, full.y0 - 8, full.x1 + 10, full.y1 + 10), page_rect)

    page_window = page_rect.height * 0.42
    content_rects = [
        rect for rect in [*text_rects, *drawing_rects]
        if _iou(rect, caption) < 0.05 and rect.width >= 8 and rect.height >= 1.5
        and not any(_iou(rect, other) > 0.08 for other in other_caption_rects)
    ]
    below = [
        rect for rect in content_rects
        if rect.y0 >= caption.y1 - 2
        and rect.y1 <= upper
        and rect.y0 - caption.y1 <= page_window
        and _overlaps_horizontally(rect, caption, margin=170)
    ]
    above = [
        rect for rect in content_rects
        if rect.y1 <= caption.y0 + 2
        and rect.y0 >= lower
        and caption.y0 - rect.y1 <= page_window
        and _overlaps_horizontally(rect, caption, margin=170)
    ]

    selected_below = _collect_contiguous_rects(below, caption.y1, "below")
    selected_above = _collect_contiguous_rects(above, caption.y0, "above")
    selected = selected_below if selected_below else selected_above
    if not selected:
        return None

    content_union = _rect_union(selected)
    full = _rect_union([caption, content_union])
    clip = _clip_to_page(fitz.Rect(full.x0 - 12, full.y0 - 8, full.x1 + 12, full.y1 + 10), page_rect)
    if clip.width < 160 or clip.height < 60 or clip.width * clip.height < 8_000:
        return None
    return clip


def _expand_detected_table(
    page: fitz.Page,
    caption: fitz.Rect,
    previous_caption: fitz.Rect | None,
    next_caption: fitz.Rect | None,
    seed: fitz.Rect,
    text_rects: list[fitz.Rect],
    drawing_rects: list[fitz.Rect],
    other_caption_rects: list[fitz.Rect],
) -> fitz.Rect | None:
    page_rect = page.rect
    lower = previous_caption.y1 + 6 if previous_caption else page_rect.y0
    upper = next_caption.y0 - 6 if next_caption else page_rect.y1
    content_rects = [
        rect for rect in [*text_rects, *drawing_rects]
        if _iou(rect, caption) < 0.05
        and not any(_iou(rect, other) > 0.08 for other in other_caption_rects)
        and rect.width >= 6
        and rect.height >= 1.0
        and (
            _overlaps_horizontally(rect, seed, margin=80)
            or _overlaps_horizontally(rect, caption, margin=170)
        )
    ]
    seed_is_above = seed.y1 <= caption.y0 + 3
    if seed_is_above:
        candidates = [
            rect for rect in content_rects
            if rect.y1 <= caption.y0 + 3
            and rect.y0 >= lower
            and rect.y1 >= seed.y0 - page_rect.height * 0.38
        ]
        selected = _collect_contiguous_rects(candidates, caption.y0, "above", max_gap=36)
    else:
        candidates = [
            rect for rect in content_rects
            if rect.y0 >= caption.y1 - 3
            and rect.y1 <= upper
            and rect.y0 <= seed.y1 + page_rect.height * 0.38
        ]
        selected = _collect_contiguous_rects(candidates, caption.y1, "below", max_gap=36)
    if not selected:
        return seed
    expanded = _rect_union([seed, *_merge_nearby_label_rects(selected, content_rects)])
    if expanded.width * expanded.height < seed.width * seed.height:
        return seed
    return expanded


def _extract_layout_figures(doc: fitz.Document, paper_dir: Path) -> list[dict[str, object]]:
    figures: list[dict[str, object]] = []
    crops_by_page: dict[int, list[fitz.Rect]] = {}
    for page_index in range(doc.page_count):
        page = doc[page_index]
        try:
            blocks = page.get_text("dict").get("blocks", [])
        except Exception:
            continue
        captions = _caption_blocks(blocks)
        if not captions:
            continue
        image_rects = _image_rects(page, blocks)
        text_rects = [
            fitz.Rect(block.get("bbox", (0, 0, 0, 0)))
            for block in blocks
            if block.get("type") == 0 and _block_text(block)
        ]
        drawing_rects = _drawing_rects(page)
        for index, (caption_rect, _caption_text, kind) in enumerate(captions):
            prev_caption = captions[index - 1][0] if index > 0 else None
            next_caption = captions[index + 1][0] if index + 1 < len(captions) else None
            other_caption_rects = [rect for caption_index, (rect, _text, _kind) in enumerate(captions) if caption_index != index]
            if kind == "table":
                clip = _table_crop_for_caption(
                    page,
                    caption_rect,
                    prev_caption,
                    next_caption,
                    text_rects,
                    drawing_rects,
                    other_caption_rects,
                )
            else:
                clip = _figure_crop_for_caption(
                    page,
                    caption_rect,
                    prev_caption,
                    next_caption,
                    image_rects,
                    text_rects,
                    drawing_rects,
                )
            if not clip:
                continue
            existing = crops_by_page.setdefault(page_index, [])
            if any(_iou(clip, old) > 0.65 for old in existing):
                continue
            existing.append(clip)
            figure_index = len(figures) + 1
            dest = paper_dir / f"fig_{figure_index:03d}.jpg"
            size = _save_page_crop(page, clip, dest)
            if not size:
                dest.unlink(missing_ok=True)
                continue
            figures.append(
                {
                    "name": dest.name,
                    "page": page_index + 1,
                    "width": size[0],
                    "height": size[1],
                    "source": kind,
                }
            )
            if len(figures) >= MAX_FIGURES:
                return figures
    return figures


def _extract_embedded_images(doc: fitz.Document, paper_dir: Path) -> list[dict[str, object]]:
    figures: list[dict[str, object]] = []
    seen_xrefs: set[int] = set()
    for page_index in range(doc.page_count):
        page = doc[page_index]
        for img in page.get_images(full=True):
            xref = img[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            try:
                info = doc.extract_image(xref)
            except Exception:
                continue
            if not _passes_filter(info.get("width", 0), info.get("height", 0)):
                continue
            index = len(figures) + 1
            dest = paper_dir / f"fig_{index:03d}.jpg"
            size = _save_image(info["image"], dest)
            if not size:
                continue
            figures.append(
                {
                    "name": dest.name,
                    "page": page_index + 1,
                    "width": size[0],
                    "height": size[1],
                    "source": "embedded",
                }
            )
            if len(figures) >= MAX_FIGURES:
                return figures
    return figures


def figure_file_path(paper_id: int, name: str) -> Path | None:
    if not name.startswith("fig_") or not name.endswith(".jpg") or "/" in name or ".." in name:
        return None
    path = _paper_dir(paper_id) / name
    return path if path.exists() else None


def cache_stats() -> dict[str, object]:
    root = _cache_root()
    total = 0
    papers = 0
    for paper_dir in root.iterdir() if root.exists() else []:
        if not paper_dir.is_dir():
            continue
        papers += 1
        for file in paper_dir.glob("*"):
            try:
                total += file.stat().st_size
            except OSError:
                pass
    return {"papers": papers, "bytes": total}


def clear_cache() -> dict[str, object]:
    import shutil

    root = _cache_root()
    stats = cache_stats()
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    _cache_root()
    return {"ok": True, "cleared_papers": stats["papers"], "cleared_bytes": stats["bytes"]}
