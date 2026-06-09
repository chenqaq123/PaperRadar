#!/usr/bin/env python3
"""Fetch accepted CVPR 2026 papers from the official CVPR virtual site.

The script:
1. Discovers the real JSON endpoints from the official papers page.
2. Downloads accepted paper metadata and abstracts.
3. Exports both JSON and CSV.
4. Optionally downloads PDFs once the official site exposes them.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


BASE_URL = "https://cvpr.thecvf.com"
USER_AGENT = (
    "Mozilla/5.0 (compatible; CVPR2026AcceptedPapersBot/1.0; "
    "+https://cvpr.thecvf.com/)"
)


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


def discover_data_urls(year: int) -> Tuple[str, str]:
    papers_page_url = f"{BASE_URL}/virtual/{year}/papers.html"
    html = fetch_text(papers_page_url)

    match = re.search(
        r'start\("(?P<papers>[^"]+\.json)"\s*,\s*(?:true|false)\s*,\s*"(?P<abstracts>[^"]+\.json)"\)',
        html,
    )
    if not match:
        raise RuntimeError(
            "Could not locate the official JSON endpoints in papers.html. "
            "The page structure may have changed."
        )

    papers_url = urljoin(BASE_URL, match.group("papers"))
    abstracts_url = urljoin(BASE_URL, match.group("abstracts"))
    return papers_url, abstracts_url


def normalize_paper(raw: Dict, abstract_map: Dict[str, str]) -> Dict:
    authors = raw.get("authors") or []
    author_names = [item.get("fullname", "").strip() for item in authors if item.get("fullname")]
    institutions = [
        item.get("institution", "").strip()
        for item in authors
        if item.get("institution")
    ]
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
        "virtualsite_url": urljoin(BASE_URL, virtualsite_url) if virtualsite_url else "",
        "paper_pdf_url": urljoin(BASE_URL, pdf_url) if pdf_url.startswith("/") else pdf_url,
        "abstract": abstract,
    }


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


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w\-.]+", "_", name, flags=re.UNICODE).strip("._")
    return cleaned[:150] or "paper"


def download_pdfs(papers: Iterable[Dict], out_dir: Path, limit: int | None = None) -> Tuple[int, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    attempted = 0
    downloaded = 0

    for paper in papers:
        pdf_url = paper.get("paper_pdf_url")
        if not pdf_url:
            continue
        attempted += 1
        if limit is not None and attempted > limit:
            break

        filename = f"{paper['id']}_{safe_filename(paper['title'])}.pdf"
        path = out_dir / filename
        if path.exists():
            downloaded += 1
            continue

        request = Request(pdf_url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(request, timeout=120) as response:
                path.write_bytes(response.read())
            downloaded += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] failed to download PDF for {paper['id']}: {exc}", file=sys.stderr)

    return attempted, downloaded


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2026, help="Conference year, default: 2026")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output") / "cvpr2026",
        help="Directory to store exported files",
    )
    parser.add_argument(
        "--download-pdfs",
        action="store_true",
        help="Download PDFs when the official metadata provides a paper_pdf_url",
    )
    parser.add_argument(
        "--pdf-limit",
        type=int,
        default=None,
        help="Download at most N PDFs, useful for testing",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    papers_url, abstracts_url = discover_data_urls(args.year)
    papers_payload = fetch_json(papers_url)
    abstracts_payload = fetch_json(abstracts_url)

    abstract_map = {
        str(key): value
        for key, value in (abstracts_payload.items() if isinstance(abstracts_payload, dict) else [])
    }
    raw_papers = papers_payload.get("results", [])
    accepted_papers = [normalize_paper(item, abstract_map) for item in raw_papers]

    json_path = output_dir / f"cvpr{args.year}_accepted_papers.json"
    csv_path = output_dir / f"cvpr{args.year}_accepted_papers.csv"
    export_json(accepted_papers, json_path)
    export_csv(accepted_papers, csv_path)

    print(f"Discovered papers JSON: {papers_url}")
    print(f"Discovered abstracts JSON: {abstracts_url}")
    print(f"Total accepted papers exported: {len(accepted_papers)}")
    print(f"JSON saved to: {json_path}")
    print(f"CSV saved to: {csv_path}")

    if args.download_pdfs:
        attempted, downloaded = download_pdfs(
            accepted_papers,
            output_dir / "pdfs",
            limit=args.pdf_limit,
        )
        print(f"PDFs with exposed URLs: {attempted}")
        print(f"PDFs downloaded/already present: {downloaded}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
