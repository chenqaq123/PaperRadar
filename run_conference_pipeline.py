#!/usr/bin/env python3
"""Unified entrypoint: from conference name to final HTML outputs."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from conference_registry import make_run_slug, resolve_conference


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conference", required=True, help="Conference key, e.g. cvpr/iccv/wacv")
    parser.add_argument("--year", type=int, required=True, help="Conference year, e.g. 2026")
    parser.add_argument("--translate", action="store_true", help="Translate paper titles via API")
    parser.add_argument("--translate-abstracts", action="store_true", help="Also translate abstracts")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("GPT_API_URL", "https://api.gpt.ge/v1/chat/completions"),
        help="OpenAI-compatible chat completions endpoint",
    )
    parser.add_argument("--api-key", default=os.environ.get("GPT_API_KEY", ""), help="API key, preferably via env var")
    parser.add_argument("--model", default="qwen3.6-plus", help="Translation model")
    parser.add_argument("--translation-limit", type=int, default=None, help="Translate at most N rows, useful for testing")
    parser.add_argument("--translation-batch-size", type=int, default=15, help="How many papers to translate per API request")
    return parser.parse_args()


def run_cmd(cmd: list[str]) -> None:
    print(f"[run] {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def main() -> int:
    args = parse_args()
    spec = resolve_conference(args.conference)
    slug = make_run_slug(spec.key, args.year)
    conference_label = f"{spec.display_name} {args.year}"

    data_dir = Path("output") / slug
    analysis_dir = Path("output") / f"{slug}_analysis"
    english_csv = data_dir / f"{slug}_accepted_papers.csv"
    translated_csv = data_dir / f"{slug}_accepted_papers_zh.csv"
    translation_cache = data_dir / f"translation_cache_{args.model.replace('/', '_')}.jsonl"

    run_cmd([
        sys.executable,
        "crawl_conference_accepted.py",
        "--conference", spec.key,
        "--year", str(args.year),
        "--output-dir", str(data_dir),
        "--output-prefix", slug,
    ])

    run_cmd([
        sys.executable,
        "analyze_cvpr2026_research_directions.py",
        "--input", str(english_csv),
        "--output-dir", str(analysis_dir),
    ])

    if args.translate:
        if not args.api_key.strip():
            raise RuntimeError("Translation requested but no API key provided. Pass --api-key or set GPT_API_KEY.")
        translate_cmd = [
            sys.executable,
            "translate_cvpr2026_with_api.py",
            "--input", str(english_csv),
            "--output", str(translated_csv),
            "--cache", str(translation_cache),
            "--api-url", args.api_url,
            "--api-key", args.api_key,
            "--model", args.model,
            "--batch-size", str(args.translation_batch_size),
        ]
        if args.translation_limit is not None:
            translate_cmd.extend(["--limit", str(args.translation_limit)])
        if args.translate_abstracts:
            translate_cmd.append("--translate-abstracts")
        run_cmd(translate_cmd)

    run_cmd([
        sys.executable,
        "render_conference_html_report.py",
        "--conference-label", conference_label,
        "--analysis-dir", str(analysis_dir),
        "--source-csv", str(english_csv),
        "--output", str(analysis_dir / "report.html"),
    ])

    run_cmd([
        sys.executable,
        "build_papers_viewer.py",
        "--conference-label", conference_label,
        "--analysis-dir", str(analysis_dir),
        "--english-csv", str(english_csv),
        "--translated-csv", str(translated_csv),
    ])

    print("")
    print(f"[done] Final report HTML: {analysis_dir / 'report.html'}")
    print(f"[done] Paper viewer HTML: {analysis_dir / 'papers_viewer.html'}")
    if args.translate:
        print(f"[done] Translated CSV: {translated_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
