#!/usr/bin/env python3
"""Fetch supported conference proceedings, import them, and run Paper Radar ranking."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

from conference_registry import CONFERENCE_REGISTRY, make_run_slug, resolve_conference
from crawl_conference_accepted import crawl_papers, export_csv, export_json
from app.db import connect, init_db
from app.importers import import_conference_csv
from app.matcher import run_matching


DEFAULT_YEARS = {
    "cvpr": [2022, 2023, 2024, 2025, 2026],
    "eccv": [2022, 2024],
    "iccv": [2021, 2023, 2025],
    "iclr": [2021, 2022, 2023, 2024, 2025],
    "icml": [2021, 2022, 2023, 2024, 2025],
    "neurips": [2021, 2022, 2023, 2024, 2025],
    "acl": [2021, 2022, 2023, 2024, 2025],
    "emnlp": [2021, 2022, 2023, 2024, 2025],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--conference",
        action="append",
        choices=sorted(DEFAULT_YEARS),
        help="Conference key. May be repeated. Default: cvpr, eccv, iccv, iclr, icml, neurips, acl, emnlp.",
    )
    parser.add_argument(
        "--years",
        default=None,
        help="Comma-separated years to use for every selected conference. Default: each conference's stable recent years.",
    )
    parser.add_argument("--limit-per-profile", type=int, default=100, help="Stored recommendation count per profile.")
    parser.add_argument("--detail-workers", type=int, default=100, help="Concurrent detail fetchers for NeurIPS/CVF/ECVA details.")
    parser.add_argument("--max-papers", type=int, default=None, help="Optional cap for smoke tests.")
    parser.add_argument("--skip-ranking", action="store_true", help="Only fetch and import CSV files.")
    parser.add_argument("--force-fetch", action="store_true", help="Fetch again even when output CSV already exists.")
    return parser.parse_args()


def selected_jobs(args: argparse.Namespace) -> list[tuple[str, int]]:
    conferences = args.conference or ["cvpr", "eccv", "iccv", "iclr", "icml", "neurips", "acl", "emnlp"]
    forced_years = [int(item.strip()) for item in args.years.split(",") if item.strip()] if args.years else None
    jobs = []
    for conference in conferences:
        years = forced_years or DEFAULT_YEARS[conference]
        for year in years:
            jobs.append((conference, year))
    return jobs


def ensure_csv(conference: str, year: int, detail_workers: int, max_papers: int | None, force_fetch: bool) -> Path:
    spec = resolve_conference(conference)
    slug = make_run_slug(conference, year)
    output_dir = ROOT / "output" / slug
    output_prefix = slug
    csv_path = output_dir / f"{output_prefix}_accepted_papers.csv"
    json_path = output_dir / f"{output_prefix}_accepted_papers.json"
    if csv_path.exists() and not force_fetch:
        print(f"[skip] Using existing CSV: {csv_path}")
        return csv_path
    output_dir.mkdir(parents=True, exist_ok=True)
    papers, source_description = crawl_papers(spec, year, detail_workers=detail_workers, max_papers=max_papers)
    export_json(papers, json_path)
    export_csv(papers, csv_path)
    print(f"[fetch] {spec.display_name} {year}: {len(papers)} papers")
    print(f"[fetch] {source_description}")
    return csv_path


def main() -> int:
    args = parse_args()
    unknown = sorted(set(args.conference or []) - set(CONFERENCE_REGISTRY))
    if unknown:
        raise SystemExit(f"Unsupported conference(s): {', '.join(unknown)}")

    conn = connect()
    init_db(conn)
    try:
        for conference, year in selected_jobs(args):
            print(f"\n=== {conference.upper()} {year} ===")
            csv_path = ensure_csv(conference, year, args.detail_workers, args.max_papers, args.force_fetch)
            csv_text = csv_path.read_text(encoding="utf-8")
            summary = import_conference_csv(conn, csv_text, conference, year)
            print(f"[import] {summary.imported}/{summary.total} papers imported")
            if not args.skip_ranking:
                result = run_matching(conn, conference, year, args.limit_per_profile)
                print(
                    "[rank] "
                    f"run_id={result['run_id']} profiles={result['profiles']} "
                    f"papers={result['papers']} results={result['results']}"
                )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
