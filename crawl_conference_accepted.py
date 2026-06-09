#!/usr/bin/env python3
"""Fetch accepted papers from supported conference virtual sites."""

from __future__ import annotations

import argparse
import csv
import html as html_lib
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from conference_registry import make_run_slug, resolve_conference


USER_AGENT = "Mozilla/5.0 (compatible; ConferencePaperCrawler/1.0)"


def fetch_text(url: str, timeout: int = 60, retries: int = 3) -> str:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        request = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt == retries:
                break
            time.sleep(min(attempt, 3))
    raise RuntimeError(f"Failed to fetch {url}: {last_error}") from last_error


def fetch_json(url: str) -> Dict:
    return json.loads(fetch_text(url))


def clean_latex(value: str) -> str:
    value = value.replace("\n", " ")
    value = re.sub(r"[{}]", "", value)
    value = value.replace("\\&", "&")
    value = value.replace("\\_", "_")
    value = value.replace("\\%", "%")
    value = value.replace("\\$", "$")
    value = value.replace("\\#", "#")
    value = value.replace("\\textbf", "")
    value = value.replace("\\emph", "")
    value = re.sub(r"\\['`^~\"c]\s*\{?([A-Za-z])\}?", r"\1", value)
    value = re.sub(r"\s+", " ", value)
    return html_lib.unescape(value).strip()


def parse_bibtex(text: str) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    entry_pattern = re.compile(r"^@(?P<type>\w+)\s*[{(](?P<body>.*?)(?=^@\w+\s*[{(]|\Z)", re.MULTILINE | re.DOTALL)
    for match in entry_pattern.finditer(text):
        entry_type = match.group("type").lower()
        body = match.group("body").strip()
        if entry_type != "inproceedings" or "," not in body:
            continue
        body = re.sub(r"\s*[})]\s*$", "", body, flags=re.DOTALL)
        key, fields_text = body.split(",", 1)
        entry: Dict[str, str] = {"source_key": key.strip(), "entry_type": entry_type}
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
            entry[name] = clean_latex(value)
        if entry.get("title"):
            entries.append(entry)
    return entries


def normalize_bibtex_authors(value: str) -> List[str]:
    authors = []
    for raw in re.split(r"\s+and\s+", value or ""):
        name = clean_latex(raw).strip()
        if not name:
            continue
        if "," in name:
            parts = [part.strip() for part in name.split(",") if part.strip()]
            if len(parts) == 2:
                name = f"{parts[1]} {parts[0]}"
            elif len(parts) > 2:
                name = " ".join(parts[1:] + [parts[0]])
        authors.append(name)
    return authors


def value_from_openreview(content: Dict, key: str, default=""):
    value = content.get(key, default)
    if isinstance(value, dict) and "value" in value:
        return value.get("value", default)
    return value


def fetch_pmlr_bib_papers(base_url: str, display_name: str, year: int, volume: int) -> List[Dict]:
    bib_url = f"{base_url}/v{volume}/assets/bib/bibliography.bib"
    entries = parse_bibtex(fetch_text(bib_url, timeout=120, retries=3))
    papers = []
    for index, entry in enumerate(entries, start=1):
        source_key = entry.get("source_key") or f"pmlr-v{volume}-{index}"
        papers.append(
            {
                "id": source_key,
                "uid": source_key,
                "title": entry.get("title", ""),
                "authors": normalize_bibtex_authors(entry.get("author", "")),
                "institutions": [],
                "decision": "Accept",
                "eventtype": "Paper",
                "topic": entry.get("booktitle", f"Proceedings of {display_name} {year}"),
                "keywords": [],
                "sourceurl": bib_url,
                "openreview_group": "",
                "virtualsite_url": entry.get("url", f"{base_url}/v{volume}/"),
                "paper_pdf_url": entry.get("pdf", ""),
                "abstract": strip_tags(entry.get("abstract", "")),
            }
        )
    print(f"[pmlr] Discovered {len(papers)} papers from {bib_url}")
    return papers


def normalize_openreview_note(note: Dict, display_name: str, year: int) -> Dict:
    content = note.get("content") or {}
    title = value_from_openreview(content, "title", "")
    authors = value_from_openreview(content, "authors", []) or []
    keywords = value_from_openreview(content, "keywords", []) or []
    abstract = value_from_openreview(content, "abstract", "") or ""
    venue = value_from_openreview(content, "venue", "") or value_from_openreview(content, "venueid", "")
    primary_area = value_from_openreview(content, "primary_area", "") or value_from_openreview(
        content, "Please_choose_the_closest_area_that_your_submission_falls_into", ""
    )
    pdf = value_from_openreview(content, "pdf", "") or ""
    forum = note.get("forum") or note.get("id") or ""
    if isinstance(authors, str):
        authors = normalize_bibtex_authors(authors)
    if isinstance(keywords, str):
        keywords = [item.strip() for item in re.split(r"[;,]", keywords) if item.strip()]
    pdf_url = urljoin("https://openreview.net", pdf) if pdf.startswith("/") else pdf
    return {
        "id": forum,
        "uid": forum,
        "title": clean_latex(title),
        "authors": [clean_latex(str(author)) for author in authors],
        "institutions": [],
        "decision": "Accept",
        "eventtype": venue or "Paper",
        "topic": clean_latex(str(primary_area)),
        "keywords": [clean_latex(str(keyword)) for keyword in keywords],
        "sourceurl": f"https://openreview.net/forum?id={forum}" if forum else f"https://openreview.net/group?id={display_name}.cc/{year}/Conference",
        "openreview_group": f"{display_name}.cc/{year}/Conference",
        "virtualsite_url": f"https://openreview.net/forum?id={forum}" if forum else "",
        "paper_pdf_url": pdf_url,
        "abstract": clean_latex(abstract),
    }


def openreview_api_url(api_base: str, params: Dict[str, object]) -> str:
    return f"{api_base}/notes?{urlencode(params, doseq=True)}"


def fetch_openreview_notes(api_base: str, params: Dict[str, object], limit: int = 1000) -> List[Dict]:
    notes: List[Dict] = []
    offset = 0
    total: int | None = None
    while True:
        page_params = {**params, "limit": limit, "offset": offset}
        payload = fetch_json(openreview_api_url(api_base, page_params))
        page = payload.get("notes", [])
        if total is None:
            total = payload.get("count")
        notes.extend(page)
        if not page or (total is not None and len(notes) >= total):
            break
        offset += len(page)
        print(f"[openreview] Notes fetched: {len(notes)}/{total or '?'}")
    return notes


def fetch_iclr_openreview_papers(display_name: str, year: int) -> List[Dict]:
    if year >= 2024:
        notes = fetch_openreview_notes(
            "https://api2.openreview.net",
            {"content.venueid": f"ICLR.cc/{year}/Conference"},
        )
    else:
        venue_variants = [
            f"ICLR {year} poster",
            f"ICLR {year} Poster",
            f"ICLR {year} oral",
            f"ICLR {year} Oral",
            f"ICLR {year} spotlight",
            f"ICLR {year} Spotlight",
        ]
        seen: set[str] = set()
        notes = []
        for venue in venue_variants:
            page = fetch_openreview_notes(
                "https://api.openreview.net",
                {
                    "invitation": f"ICLR.cc/{year}/Conference/-/Blind_Submission",
                    "content.venue": venue,
                },
                limit=1000,
            )
            for note in page:
                note_id = note.get("forum") or note.get("id")
                if note_id and note_id not in seen:
                    seen.add(note_id)
                    notes.append(note)
    papers = [normalize_openreview_note(note, display_name, year) for note in notes]
    print(f"[openreview] Discovered {len(papers)} accepted papers for {display_name} {year}")
    return papers


def discover_cvf_virtual_data_urls(base_url: str, year: int) -> Tuple[str, str]:
    papers_page_url = f"{base_url}/virtual/{year}/papers.html"
    html = fetch_text(papers_page_url)
    match = re.search(
        r'start\("(?P<papers>[^"]+\.json)"\s*,\s*(?:true|false)\s*,\s*"(?P<abstracts>[^"]+\.json)"\)',
        html,
    )
    if not match:
        raise RuntimeError(
            f"Could not locate papers/abstracts JSON endpoints in {papers_page_url}. "
            "The conference page structure may have changed."
        )
    return (
        urljoin(base_url, match.group("papers")),
        urljoin(base_url, match.group("abstracts")),
    )


def normalize_paper(base_url: str, raw: Dict, abstract_map: Dict[str, str]) -> Dict:
    authors = raw.get("authors") or []
    author_names = [item.get("fullname", "").strip() for item in authors if item.get("fullname")]
    institutions = [item.get("institution", "").strip() for item in authors if item.get("institution")]
    paper_id = str(raw.get("id"))
    abstract = raw.get("abstract") or abstract_map.get(paper_id) or ""
    virtualsite_url = raw.get("virtualsite_url") or ""
    pdf_url = raw.get("paper_pdf_url") or raw.get("paper_url") or ""

    return {
        "id": raw.get("id"),
        "uid": raw.get("uid"),
        "title": raw.get("name"),
        "authors": author_names,
        "institutions": institutions,
        "decision": raw.get("decision"),
        "eventtype": raw.get("eventtype"),
        "topic": raw.get("topic"),
        "keywords": raw.get("keywords") or [],
        "sourceurl": raw.get("sourceurl"),
        "openreview_group": raw.get("sourceurl"),
        "virtualsite_url": urljoin(base_url, virtualsite_url) if virtualsite_url else "",
        "paper_pdf_url": urljoin(base_url, pdf_url) if pdf_url.startswith("/") else pdf_url,
        "abstract": abstract,
    }


def strip_tags(value: str) -> str:
    value = re.sub(r"</?(?:br|p|div|li|dt|dd|section|h\d)\b[^>]*>", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    value = html_lib.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def fetch_openaccess_abstract(html_url: str) -> str:
    try:
        html = fetch_text(html_url, timeout=45, retries=2)
    except Exception:
        return ""
    match = re.search(r'<div id="abstract">\s*(?P<abstract>.*?)\s*</div>', html, flags=re.DOTALL | re.IGNORECASE)
    return strip_tags(match.group("abstract")) if match else ""


def fetch_openaccess_papers(display_name: str, year: int, workers: int = 100, max_papers: int | None = None) -> List[Dict]:
    base_url = "https://openaccess.thecvf.com"
    proceedings_url = f"{base_url}/{display_name.upper()}{year}?day=all"
    html = fetch_text(proceedings_url)
    pattern = re.compile(
        r'<dt class="ptitle"><br><a href="(?P<html_url>[^"]+)">(?P<title>.*?)</a></dt>\s*'
        r"<dd>(?P<authors_html>.*?)</dd>\s*"
        r"<dd>\s*(?P<links_html>.*?)</dd>",
        flags=re.DOTALL | re.IGNORECASE,
    )
    entries = []
    for index, match in enumerate(pattern.finditer(html), start=1):
        title = strip_tags(match.group("title"))
        detail_url = urljoin(base_url, match.group("html_url"))
        authors = [
            html_lib.unescape(item).strip()
            for item in re.findall(r'name="query_author"\s+value="([^"]+)"', match.group("authors_html"))
        ]
        pdf_url = ""
        pdf_match = re.search(r'href="(?P<pdf>[^"]+_paper\.pdf)"', match.group("links_html"), flags=re.IGNORECASE)
        if pdf_match:
            pdf_url = urljoin(base_url, pdf_match.group("pdf"))
        paper_id = Path(match.group("html_url")).stem or f"{display_name.lower()}{year}_{index}"
        entries.append(
            {
                "id": paper_id,
                "uid": paper_id,
                "title": title,
                "authors": authors,
                "institutions": [],
                "decision": "Accept",
                "eventtype": "Paper",
                "topic": "",
                "keywords": [],
                "sourceurl": proceedings_url,
                "openreview_group": "",
                "virtualsite_url": detail_url,
                "paper_pdf_url": pdf_url,
                "abstract": "",
            }
        )
        if max_papers and len(entries) >= max_papers:
            break

    print(f"[openaccess] Discovered {len(entries)} papers from {proceedings_url}")
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        future_to_index = {
            executor.submit(fetch_openaccess_abstract, entry["virtualsite_url"]): index
            for index, entry in enumerate(entries)
        }
        completed = 0
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            entries[index]["abstract"] = future.result()
            completed += 1
            if completed == len(entries) or completed % 250 == 0:
                print(f"[openaccess] Abstracts fetched: {completed}/{len(entries)}")
    return entries


def normalize_neurips_detail(base_url: str, detail_url: str) -> Tuple[str, str]:
    try:
        html = fetch_text(detail_url, timeout=45, retries=2)
    except Exception:
        return "", ""
    abstract = ""
    abstract_match = re.search(
        r'<p class="paper-abstract">\s*(?P<abstract>.*?)\s*</p>\s*</section>',
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if abstract_match:
        abstract = strip_tags(abstract_match.group("abstract"))
    pdf_url = ""
    pdf_match = re.search(r'<meta name="citation_pdf_url" content="(?P<pdf>[^"]+)"', html)
    if pdf_match:
        pdf_url = pdf_match.group("pdf")
    else:
        pdf_match = re.search(r"href='(?P<pdf>[^']+-Paper[^']+\.pdf)'", html)
        if pdf_match:
            pdf_url = urljoin(base_url, pdf_match.group("pdf"))
    return abstract, pdf_url


def fetch_neurips_papers(base_url: str, display_name: str, year: int, workers: int = 100, max_papers: int | None = None) -> List[Dict]:
    proceedings_url = f"{base_url}/paper_files/paper/{year}"
    html = fetch_text(proceedings_url, timeout=120, retries=3)
    blocks = [
        match.group(0)
        for match in re.finditer(r"<li\b[^>]*data-track=\"[^\"]*\"[^>]*>.*?</li>", html, flags=re.DOTALL | re.IGNORECASE)
    ]
    entries = []
    for index, block in enumerate(blocks, start=1):
        link_match = re.search(r'<a title="paper title" href="(?P<href>[^"]+)">(?P<title>.*?)</a>', block, flags=re.DOTALL | re.IGNORECASE)
        authors_match = re.search(r'<span class="paper-authors">(?P<authors>.*?)</span>', block, flags=re.DOTALL | re.IGNORECASE)
        if not link_match or not authors_match:
            continue
        track_label_match = re.search(r'<span class="paper-track-badge">(?P<track_label>.*?)</span>', block, flags=re.DOTALL | re.IGNORECASE)
        track_label = strip_tags(track_label_match.group("track_label")) if track_label_match else "Main Conference Track"
        detail_url = urljoin(base_url, link_match.group("href"))
        paper_hash = Path(link_match.group("href")).name.split("-Abstract", 1)[0]
        authors = [item.strip() for item in strip_tags(authors_match.group("authors")).split(",") if item.strip()]
        entries.append(
            {
                "id": paper_hash or f"neurips{year}_{index}",
                "uid": paper_hash or f"neurips{year}_{index}",
                "title": strip_tags(link_match.group("title")),
                "authors": authors,
                "institutions": [],
                "decision": "Accept",
                "eventtype": track_label,
                "topic": track_label,
                "keywords": [],
                "sourceurl": proceedings_url,
                "openreview_group": "",
                "virtualsite_url": detail_url,
                "paper_pdf_url": "",
                "abstract": "",
            }
        )
        if max_papers and len(entries) >= max_papers:
            break
    print(f"[neurips] Discovered {len(entries)} papers from {proceedings_url}")
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        future_to_index = {
            executor.submit(normalize_neurips_detail, base_url, entry["virtualsite_url"]): index
            for index, entry in enumerate(entries)
        }
        completed = 0
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            abstract, pdf_url = future.result()
            entries[index]["abstract"] = abstract
            entries[index]["paper_pdf_url"] = pdf_url
            completed += 1
            if completed == len(entries) or completed % 250 == 0:
                print(f"[neurips] Details fetched: {completed}/{len(entries)}")
    return entries


def fetch_ecva_detail(detail_url: str) -> str:
    try:
        html = fetch_text(detail_url, timeout=45, retries=2)
    except Exception:
        return ""
    match = re.search(r'<div id="abstract">\s*(?P<abstract>.*?)\s*</div>', html, flags=re.DOTALL | re.IGNORECASE)
    return strip_tags(match.group("abstract")).strip('"') if match else ""


def fetch_ecva_papers(base_url: str, display_name: str, year: int, workers: int = 100, max_papers: int | None = None) -> List[Dict]:
    proceedings_url = f"{base_url}/papers.php?conf=eccv&year={year}"
    html = fetch_text(proceedings_url, timeout=120, retries=3)
    start_marker = f"<!-- ECCV {year} -->"
    start = html.find(start_marker)
    section = html[start:] if start >= 0 else html
    next_marker = re.search(r"<!-- ECCV \d{4} -->", section[len(start_marker) :])
    if next_marker:
        section = section[: len(start_marker) + next_marker.start()]
    pattern = re.compile(
        r'<dt class="ptitle">\s*<br>\s*'
        r'<a href=["\']?(?P<detail>[^"\'>\s]+)["\']?>\s*(?P<title>.*?)</a>\s*'
        r"</dt>\s*<dd>\s*(?P<authors>.*?)</dd>\s*"
        r"<dd>\s*\[\s*<a href=['\"](?P<pdf>[^'\"]+\.pdf)['\"]>pdf</a>\s*\]",
        flags=re.DOTALL | re.IGNORECASE,
    )
    entries = []
    for index, match in enumerate(pattern.finditer(section), start=1):
        detail_url = urljoin(f"{base_url}/", match.group("detail"))
        pdf_url = urljoin(f"{base_url}/", match.group("pdf"))
        paper_id = Path(match.group("pdf")).stem or f"eccv{year}_{index}"
        authors = [
            item.strip().rstrip("*").strip()
            for item in strip_tags(match.group("authors")).split(",")
            if item.strip()
        ]
        entries.append(
            {
                "id": paper_id,
                "uid": paper_id,
                "title": strip_tags(match.group("title")),
                "authors": authors,
                "institutions": [],
                "decision": "Accept",
                "eventtype": "Paper",
                "topic": f"{display_name} {year}",
                "keywords": [],
                "sourceurl": proceedings_url,
                "openreview_group": "",
                "virtualsite_url": detail_url,
                "paper_pdf_url": pdf_url,
                "abstract": "",
            }
        )
        if max_papers and len(entries) >= max_papers:
            break
    print(f"[ecva] Discovered {len(entries)} papers from {proceedings_url}")
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        future_to_index = {
            executor.submit(fetch_ecva_detail, entry["virtualsite_url"]): index
            for index, entry in enumerate(entries)
        }
        completed = 0
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            entries[index]["abstract"] = future.result()
            completed += 1
            if completed == len(entries) or completed % 250 == 0:
                print(f"[ecva] Details fetched: {completed}/{len(entries)}")
    return entries


def fetch_acl_anthology_papers(base_url: str, display_name: str, year: int, volume: str, max_papers: int | None = None) -> List[Dict]:
    volume_url = f"{base_url}/volumes/{volume}/"
    html = fetch_text(volume_url, timeout=120, retries=3)
    blocks = re.split(r'(?=<div class="d-sm-flex align-items-stretch mb-3">)', html)
    papers = []
    for block in blocks:
        pdf_match = re.search(r'href=(?P<quote>"?)(?P<pdf>https://aclanthology\.org/(?P<id>[^"\s]+)\.pdf)(?P=quote)', block)
        if not pdf_match:
            continue
        paper_id = pdf_match.group("id")
        if paper_id.endswith(".0"):
            continue
        title_match = re.search(r"<strong>\s*<a[^>]+href=/[^>]+>(?P<title>.*?)</a>\s*</strong>", block, flags=re.DOTALL)
        if not title_match:
            continue
        authors_html = ""
        authors_match = re.search(r"</strong>\s*<br>(?P<authors>.*?)</span>\s*</div>", block, flags=re.DOTALL)
        if authors_match:
            authors_html = authors_match.group("authors")
        authors = [item.strip() for item in strip_tags(authors_html).split("|") if item.strip()]
        abstract = ""
        abstract_match = re.search(
            r'<div class="card-body p-3 small">(?P<abstract>.*?)</div>\s*</div>',
            block,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if abstract_match:
            abstract = strip_tags(abstract_match.group("abstract"))
        papers.append(
            {
                "id": paper_id,
                "uid": paper_id,
                "title": strip_tags(title_match.group("title")),
                "authors": authors,
                "institutions": [],
                "decision": "Accept",
                "eventtype": "Long Paper" if "acl-long" in volume else "Main Paper",
                "topic": f"{display_name} {year} {volume}",
                "keywords": [],
                "sourceurl": volume_url,
                "openreview_group": "",
                "virtualsite_url": f"{base_url}/{paper_id}/",
                "paper_pdf_url": pdf_match.group("pdf"),
                "abstract": abstract,
            }
        )
        if max_papers and len(papers) >= max_papers:
            break
    print(f"[acl-anthology] Discovered {len(papers)} papers from {volume_url}")
    return papers


def crawl_papers(spec, year: int, detail_workers: int = 12, max_papers: int | None = None) -> Tuple[List[Dict], str]:
    if spec.site_type == "pmlr_bib":
        if year not in spec.pmlr_volumes:
            supported = ", ".join(str(item) for item in sorted(spec.pmlr_volumes))
            raise RuntimeError(f"No PMLR volume configured for {spec.display_name} {year}. Supported years: {supported}")
        volume = spec.pmlr_volumes[year]
        papers = fetch_pmlr_bib_papers(spec.base_url, spec.display_name, year, volume)
        if max_papers:
            papers = papers[:max_papers]
        return papers, f"PMLR bibliography: {spec.base_url}/v{volume}/assets/bib/bibliography.bib"
    if spec.site_type == "openreview_iclr":
        papers = fetch_iclr_openreview_papers(spec.display_name, year)
        if max_papers:
            papers = papers[:max_papers]
        return papers, f"OpenReview accepted notes: {spec.base_url}/group?id=ICLR.cc/{year}/Conference"
    if spec.site_type == "neurips_proceedings":
        papers = fetch_neurips_papers(spec.base_url, spec.display_name, year, detail_workers, max_papers)
        return papers, f"NeurIPS proceedings: {spec.base_url}/paper_files/paper/{year}"
    if spec.site_type == "ecva":
        papers = fetch_ecva_papers(spec.base_url, spec.display_name, year, detail_workers, max_papers)
        return papers, f"ECVA proceedings: {spec.base_url}/papers.php?conf=eccv&year={year}"
    if spec.site_type == "acl_anthology":
        if year not in spec.anthology_volumes:
            supported = ", ".join(str(item) for item in sorted(spec.anthology_volumes))
            raise RuntimeError(f"No ACL Anthology volume configured for {spec.display_name} {year}. Supported years: {supported}")
        volume = spec.anthology_volumes[year]
        papers = fetch_acl_anthology_papers(spec.base_url, spec.display_name, year, volume, max_papers)
        return papers, f"ACL Anthology volume: {spec.base_url}/volumes/{volume}/"
    if spec.site_type == "cvf_virtual":
        try:
            papers_url, abstracts_url = discover_cvf_virtual_data_urls(spec.base_url, year)
            papers_payload = fetch_json(papers_url)
            try:
                abstracts_payload = fetch_json(abstracts_url)
            except Exception as exc:
                print(f"[warn] Could not fetch abstracts JSON ({exc}); using abstracts embedded in papers JSON when available.")
                abstracts_payload = {}
            abstract_map = {
                str(key): value
                for key, value in (abstracts_payload.items() if isinstance(abstracts_payload, dict) else [])
            }
            raw_papers = papers_payload.get("results", [])
            if max_papers:
                raw_papers = raw_papers[:max_papers]
            papers = [normalize_paper(spec.base_url, item, abstract_map) for item in raw_papers]
            return papers, f"Discovered papers JSON: {papers_url}\nDiscovered abstracts JSON: {abstracts_url}"
        except Exception as exc:
            print(f"[warn] CVF virtual crawler failed for {spec.display_name} {year}: {exc}")
            print("[warn] Falling back to CVF OpenAccess.")
            papers = fetch_openaccess_papers(spec.display_name, year, workers=detail_workers, max_papers=max_papers)
            return papers, f"OpenAccess page: https://openaccess.thecvf.com/{spec.display_name.upper()}{year}?day=all"
    raise RuntimeError(f"Unsupported site type for {spec.display_name}: {spec.site_type}")


def export_json(papers: List[Dict], path: Path) -> None:
    path.write_text(json.dumps(papers, ensure_ascii=False, indent=2), encoding="utf-8")


def export_csv(papers: Iterable[Dict], path: Path) -> None:
    rows = list(papers)
    fieldnames = [
        "id",
        "uid",
        "title",
        "authors",
        "institutions",
        "decision",
        "eventtype",
        "topic",
        "keywords",
        "sourceurl",
        "openreview_group",
        "virtualsite_url",
        "paper_pdf_url",
        "abstract",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for paper in rows:
            writer.writerow(
                {
                    **paper,
                    "authors": "; ".join(paper["authors"]),
                    "institutions": "; ".join(paper["institutions"]),
                    "keywords": "; ".join(paper["keywords"]),
                }
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conference", required=True, help="Conference key, e.g. cvpr/iccv/wacv")
    parser.add_argument("--year", type=int, required=True, help="Conference year, e.g. 2026")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory. Default: output/<conference><year>",
    )
    parser.add_argument(
        "--output-prefix",
        default=None,
        help="Output file prefix. Default: <conference><year>",
    )
    parser.add_argument(
        "--detail-workers",
        type=int,
        default=100,
        help="Concurrent workers for crawlers that fetch per-paper details.",
    )
    parser.add_argument(
        "--max-papers",
        type=int,
        default=None,
        help="Optional cap for crawler smoke tests. Omit for full proceedings.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    spec = resolve_conference(args.conference)

    slug = make_run_slug(spec.key, args.year)
    output_dir = args.output_dir or (Path("output") / slug)
    output_prefix = args.output_prefix or slug
    output_dir.mkdir(parents=True, exist_ok=True)

    accepted_papers, source_description = crawl_papers(spec, args.year, args.detail_workers, args.max_papers)

    json_path = output_dir / f"{output_prefix}_accepted_papers.json"
    csv_path = output_dir / f"{output_prefix}_accepted_papers.csv"
    export_json(accepted_papers, json_path)
    export_csv(accepted_papers, csv_path)

    print(f"Conference: {spec.display_name} {args.year}")
    print(source_description)
    print(f"Total accepted papers exported: {len(accepted_papers)}")
    print(f"JSON saved to: {json_path}")
    print(f"CSV saved to: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
