#!/usr/bin/env python3
"""Translate CVPR paper titles via an OpenAI-compatible API.

The script reads the exported CSV, translates each paper's title,
and writes a new CSV with bilingual columns:
- zh_title
- zh_abstract, kept for compatibility with older title+abstract caches

It is resumable through a JSONL cache file.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("output") / "cvpr2026" / "cvpr2026_accepted_papers.csv",
        help="Source CSV with title and abstract columns",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output") / "cvpr2026" / "cvpr2026_accepted_papers_zh.csv",
        help="Translated output CSV path",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("output") / "cvpr2026" / "translation_cache_qwen36plus.jsonl",
        help="JSONL cache for resumable translation",
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("GPT_API_URL", "https://api.gpt.ge/v1/chat/completions"),
        help="Full chat completions endpoint",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("GPT_API_KEY", ""),
        help="API key. Prefer passing through GPT_API_KEY env var.",
    )
    parser.add_argument(
        "--model",
        default="qwen3.6-plus",
        help="Model name, default: qwen3.6-plus",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Translate at most N papers, useful for testing",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.4,
        help="Sleep seconds between successful requests",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Ignore cache and re-translate rows",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help="Translate this many papers per API request. Default: 5",
    )
    parser.add_argument(
        "--translate-abstracts",
        action="store_true",
        help="Also translate abstracts. Default is title-only.",
    )
    return parser.parse_args()


def normalize_api_url(url: str) -> str:
    clean = re.sub(r"\s+", "", url)
    if clean.endswith("/v1"):
        return clean + "/chat/completions"
    if clean.endswith("/chat/completions"):
        return clean
    if clean.endswith("/v1/"):
        return clean.rstrip("/") + "/chat/completions"
    if clean.endswith("/"):
        return clean + "v1/chat/completions"
    return clean + "/v1/chat/completions"


def load_rows(path: Path) -> List[Dict[str, str]]:
    return list(csv.DictReader(path.open(encoding="utf-8")))


def load_cache(path: Path) -> Dict[str, Dict[str, str]]:
    cache: Dict[str, Dict[str, str]] = {}
    if not path.exists():
        return cache
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            paper_id = str(item.get("id", "")).strip()
            zh_title = str(item.get("zh_title", "")).strip()
            zh_abstract = str(item.get("zh_abstract", "")).strip()
            if paper_id and zh_title:
                cache[paper_id] = {
                    "zh_title": zh_title,
                    "zh_abstract": zh_abstract,
                }
    return cache


def append_cache(path: Path, payload: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def extract_json_object(text: str) -> Dict[str, str]:
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in API response")
    obj = json.loads(match.group(0))
    if not isinstance(obj, dict):
        raise ValueError("Response JSON is not an object")
    return obj


def extract_json_payload(text: str):
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"(\[.*\]|\{.*\})", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON payload found in API response")
    return json.loads(match.group(0))


def build_messages(title: str, abstract: str, translate_abstracts: bool = False) -> List[Dict[str, str]]:
    if not translate_abstracts:
        return [
            {
                "role": "system",
                "content": (
                    "You are a precise academic translator. Translate computer vision paper titles "
                    "into fluent Simplified Chinese. Preserve technical terms, model names, acronyms, "
                    "dataset names, and formulas when needed. Return strict JSON only."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Translate the following CV paper title into Simplified Chinese.\n"
                    "Requirements:\n"
                    "1. Keep the original meaning accurate.\n"
                    "2. Preserve acronyms like VLM, MLLM, 3DGS, NeRF when appropriate.\n"
                    "3. Do not explain anything.\n"
                    "4. Return valid JSON only with key zh_title.\n\n"
                    f"Title:\n{title}\n"
                ),
            },
        ]

    return [
        {
            "role": "system",
            "content": (
                "You are a precise academic translator. Translate computer vision paper titles "
                "and abstracts into fluent Simplified Chinese. Preserve technical terms, model "
                "names, acronyms, dataset names, and formulas when needed. Return strict JSON only."
            ),
        },
        {
            "role": "user",
            "content": (
                "Translate the following CV paper content into Simplified Chinese.\n"
                "Requirements:\n"
                "1. Keep the original meaning accurate.\n"
                "2. Preserve acronyms like VLM, MLLM, 3DGS, NeRF when appropriate.\n"
                "3. Do not explain anything.\n"
                "4. Return valid JSON only with keys zh_title and zh_abstract.\n\n"
                f"Title:\n{title}\n\n"
                f"Abstract:\n{abstract}\n"
            ),
        },
    ]


def build_batch_messages(items: List[Dict[str, str]], translate_abstracts: bool = False) -> List[Dict[str, str]]:
    if translate_abstracts:
        payload = [
            {
                "id": item["id"],
                "title": item["title"],
                "abstract": item["abstract"],
            }
            for item in items
        ]
        output_contract = "Each item must have id, zh_title, zh_abstract."
        task_text = "Translate the following paper items into Simplified Chinese."
    else:
        payload = [
            {
                "id": item["id"],
                "title": item["title"],
            }
            for item in items
        ]
        output_contract = "Each item must have id and zh_title."
        task_text = "Translate the following paper titles into Simplified Chinese."

    return [
        {
            "role": "system",
            "content": (
                "You are a precise academic translator. Translate computer vision paper titles "
                "into fluent Simplified Chinese. Preserve technical terms, model "
                "names, acronyms, dataset names, and formulas when needed. Return strict JSON only."
            ),
        },
        {
            "role": "user",
            "content": (
                f"{task_text}\n"
                "Requirements:\n"
                "1. Keep the original meaning accurate.\n"
                "2. Preserve acronyms like VLM, MLLM, 3DGS, NeRF when appropriate.\n"
                "3. Do not explain anything.\n"
                "4. Return valid JSON only.\n"
                f"5. The output must be a JSON array. {output_contract}\n\n"
                f"{json.dumps(payload, ensure_ascii=False)}"
            ),
        },
    ]


def call_chat_api(api_url: str, api_key: str, model: str, title: str, abstract: str, translate_abstracts: bool = False, retries: int = 5) -> Dict[str, str]:
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": build_messages(title, abstract, translate_abstracts),
    }
    body = json.dumps(payload).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        request = Request(api_url, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=180) as response:
                raw = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
            obj = extract_json_object(content)
            zh_title = str(obj.get("zh_title", "")).strip()
            zh_abstract = str(obj.get("zh_abstract", "")).strip()
            if not zh_title or (translate_abstracts and not zh_abstract):
                raise ValueError("Missing required translation fields in API response")
            return {
                "zh_title": zh_title,
                "zh_abstract": zh_abstract,
            }
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == retries:
                break
            sleep_s = min(2 ** attempt, 12)
            print(f"[warn] request failed on attempt {attempt}/{retries}, retrying in {sleep_s}s: {exc}", file=sys.stderr)
            time.sleep(sleep_s)
    raise RuntimeError(f"Translation API request failed after {retries} attempts: {last_error}") from last_error


def call_batch_chat_api(
    api_url: str,
    api_key: str,
    model: str,
    items: List[Dict[str, str]],
    translate_abstracts: bool = False,
    retries: int = 5,
) -> Dict[str, Dict[str, str]]:
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": build_batch_messages(items, translate_abstracts),
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    expected_ids = {str(item["id"]).strip() for item in items}
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        request = Request(api_url, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=300) as response:
                raw = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
            parsed = extract_json_payload(content)
            if isinstance(parsed, dict):
                parsed = parsed.get("translations", [])
            if not isinstance(parsed, list):
                raise ValueError("Batch translation response is not a JSON array")

            translated_map: Dict[str, Dict[str, str]] = {}
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                paper_id = str(item.get("id", "")).strip()
                zh_title = str(item.get("zh_title", "")).strip()
                zh_abstract = str(item.get("zh_abstract", "")).strip()
                if paper_id and zh_title and (zh_abstract or not translate_abstracts):
                    translated_map[paper_id] = {
                        "zh_title": zh_title,
                        "zh_abstract": zh_abstract,
                    }
            missing = expected_ids - set(translated_map)
            if missing:
                raise ValueError(f"Missing translated items for ids: {sorted(missing)}")
            return translated_map
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == retries:
                break
            sleep_s = min(2 ** attempt, 12)
            print(f"[warn] batch request failed on attempt {attempt}/{retries}, retrying in {sleep_s}s: {exc}", file=sys.stderr)
            time.sleep(sleep_s)
    raise RuntimeError(f"Batch translation API request failed after {retries} attempts: {last_error}") from last_error


def write_output(path: Path, rows: List[Dict[str, str]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_output_rows(rows: List[Dict[str, str]], cache: Dict[str, Dict[str, str]], limit: int | None = None) -> List[Dict[str, str]]:
    output_rows: List[Dict[str, str]] = []
    selected_rows = rows if limit is None else rows[:limit]
    for row in selected_rows:
        paper_id = str(row.get("id", "")).strip()
        translated = cache.get(paper_id, {})
        merged = dict(row)
        merged["zh_title"] = translated.get("zh_title", "")
        merged["zh_abstract"] = translated.get("zh_abstract", "")
        output_rows.append(merged)
    return output_rows


def main() -> int:
    args = parse_args()
    api_url = normalize_api_url(args.api_url)
    api_key = args.api_key.strip()
    if not api_key:
      print("API key is required. Pass --api-key or set GPT_API_KEY.", file=sys.stderr)
      return 2

    rows = load_rows(args.input)
    cache = {} if args.overwrite else load_cache(args.cache)
    translated_count = 0
    reused_count = 0
    selected_rows = rows if args.limit is None else rows[: args.limit]
    pending_batch: List[Dict[str, str]] = []

    for index, row in enumerate(selected_rows, start=1):
        paper_id = str(row.get("id", "")).strip()
        title = str(row.get("title", "")).strip()
        abstract = str(row.get("abstract", "")).strip()
        if paper_id in cache and not args.overwrite:
            reused_count += 1
        else:
            pending_batch.append({"id": paper_id, "title": title, "abstract": abstract, "index": index})

        if len(pending_batch) >= args.batch_size:
            translated_batch = call_batch_chat_api(
                api_url=api_url,
                api_key=api_key,
                model=args.model,
                items=pending_batch,
                translate_abstracts=args.translate_abstracts,
            )
            for item in pending_batch:
                translated = translated_batch[item["id"]]
                cache[item["id"]] = translated
                append_cache(
                    args.cache,
                    {
                        "id": item["id"],
                        "zh_title": translated["zh_title"],
                        "zh_abstract": translated.get("zh_abstract", ""),
                    },
                )
                translated_count += 1
            print(f"[ok] translated batch ending at row {index} (size={len(pending_batch)})", file=sys.stderr)
            pending_batch = []
            write_output(args.output, build_output_rows(rows, cache, args.limit))
            time.sleep(args.sleep)

    if pending_batch:
        translated_batch = call_batch_chat_api(
            api_url=api_url,
            api_key=api_key,
            model=args.model,
            items=pending_batch,
            translate_abstracts=args.translate_abstracts,
        )
        for item in pending_batch:
            translated = translated_batch[item["id"]]
            cache[item["id"]] = translated
            append_cache(
                args.cache,
                {
                    "id": item["id"],
                    "zh_title": translated["zh_title"],
                    "zh_abstract": translated.get("zh_abstract", ""),
                },
            )
            translated_count += 1
        print(f"[ok] translated final batch (size={len(pending_batch)})", file=sys.stderr)

    output_rows = build_output_rows(rows, cache, args.limit)
    write_output(args.output, output_rows)
    print(f"Output CSV written to: {args.output}")
    print(f"Cache JSONL written to: {args.cache}")
    print(f"Reused from cache: {reused_count}")
    print(f"Newly translated: {translated_count}")
    print(f"Rows written: {len(output_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
