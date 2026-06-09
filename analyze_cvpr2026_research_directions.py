#!/usr/bin/env python3
"""Analyze CVPR 2026 accepted papers for hotspots and promising directions.

This script is designed to run with the current local environment:
- Python standard library
- numpy
- matplotlib

Outputs:
- hotspot phrase ranking
- topic clusters from lightweight TF-IDF + KMeans
- promising research direction ranking
- plots and a Markdown report
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

PROJECT_CACHE_DIR = Path(".cache")
PROJECT_CACHE_DIR.mkdir(exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_CACHE_DIR / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(PROJECT_CACHE_DIR))

import matplotlib.pyplot as plt
import numpy as np


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "by", "can",
    "do", "does", "for", "from", "how", "in", "into", "is", "it", "its",
    "of", "on", "or", "our", "that", "the", "their", "this", "to", "via",
    "we", "with", "within", "without", "while", "when", "where", "which",
    "yet", "using", "use", "used", "based", "new", "towards", "toward",
    "through", "across", "under", "over", "than", "these", "those", "such",
    "more", "less", "most", "many", "few", "some", "any", "all", "not",
    "paper", "papers", "work", "works", "study", "studies", "method",
    "methods", "framework", "frameworks", "approach", "approaches",
    "task", "tasks", "problem", "problems", "model", "models", "module",
    "modules", "system", "systems", "network", "networks", "learning",
    "feature", "features", "representation", "representations", "data",
    "training", "test", "testing", "results", "result", "performance",
    "achieves", "achieve", "show", "shows", "showing", "propose", "proposes",
    "proposed", "present", "presents", "novel", "simple", "efficiently",
    "effectively", "towards", "revisiting", "rethinking", "improving",
    "improved", "boosting", "leveraging", "driven", "guided", "aware",
    "end", "endtoend", "one", "two", "three", "first", "second", "will",
    "code", "released", "release", "available", "publicly", "implementation",
    "github", "anonymous", "supplementary",
    "remains", "challenging", "challenge",
}

GENERIC_TERMS = {
    "image", "images", "video", "videos", "visual", "vision", "language",
    "multimodal", "large", "deep", "object", "objects", "scene", "scenes",
    "task", "tasks", "benchmark", "dataset", "model", "models", "method",
    "methods", "framework", "frameworks", "learning", "generation",
}

RHETORICAL_TERMS = {
    "existing", "introduce", "demonstrate", "state-of-the-art", "extensive",
    "however", "address", "compare", "comparison", "outperform", "outperforms",
    "outperforming", "achieves", "achieve", "experimental", "experiments",
    "experiment", "superior", "significantly", "various", "different",
    "effective", "effectiveness", "comprehensive", "several", "further",
}

CHALLENGE_TERMS = {
    "efficient", "efficiency", "generalizable", "generalization", "robust",
    "robustness", "scalable", "scalability", "unified", "uncertainty",
    "sparse", "adaptive", "adaptation", "realtime", "openvocabulary",
    "compositional", "longtail", "dataefficient",
    "safety", "safe", "fair", "fairness", "lowlight", "occlusion",
    "fewshot", "zeroshot", "noise", "outofdistribution", "ood",
}

TREND_TOKENS = {
    "3d", "adaptation", "agent", "avatar", "compression", "depth", "detection",
    "diffusion", "editing", "embodied", "gaussian", "generation", "language",
    "llm", "medical", "mllm", "motion", "multimodal", "pose", "reasoning",
    "reconstruction", "robot", "robotics", "scene", "segmentation",
    "splatting", "temporal", "token", "tracking", "video", "vision", "vlm",
    "world",
}

DOMAIN_LEXICONS = {
    "3d": {"3d", "gaussian", "neural", "radiance", "mesh", "point", "splatting", "synthesis"},
    "generation": {"generation", "generative", "diffusion", "editing", "avatar", "synthesis"},
    "video": {"video", "temporal", "motion", "tracking", "streaming"},
    "vlm_llm": {"vlm", "llm", "mllm", "language", "instruction", "multimodal", "reasoning"},
    "robotics_agent": {"robot", "robotics", "agent", "gui", "action", "policy", "world"},
    "medical": {"medical", "clinical", "microscopy", "ct", "mri", "ultrasound", "immunofluorescent"},
    "segmentation": {"segmentation", "segmenter", "mask", "parsing", "detection"},
    "document": {"document", "doc", "ocr", "chart", "table", "layout"},
}


@dataclass
class Paper:
    paper_id: str
    title: str
    abstract: str
    authors: List[str]
    institutions: List[str]
    decision: str
    eventtype: str
    combined_text: str
    tokens: List[str]
    title_tokens: List[str]
    doc_terms: Counter
    challenge_hit: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("output") / "cvpr2026" / "cvpr2026_accepted_papers.csv",
        help="Input CSV exported from crawl_cvpr_2026_accepted.py",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output") / "cvpr2026_analysis",
        help="Directory for analysis outputs",
    )
    parser.add_argument(
        "--clusters",
        type=int,
        default=12,
        help="Number of topic clusters",
    )
    parser.add_argument(
        "--feature-size",
        type=int,
        default=420,
        help="Max number of TF-IDF features for clustering",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed",
    )
    return parser.parse_args()


def normalize_text(text: str) -> str:
    text = text.lower()
    text = text.replace("vision-language", "vision language")
    text = text.replace("large language model", "llm")
    text = text.replace("large vision language model", "vlm")
    text = text.replace("multimodal large language model", "mllm")
    text = text.replace("open-vocabulary", "openvocabulary")
    text = text.replace("few-shot", "fewshot")
    text = text.replace("zero-shot", "zeroshot")
    text = text.replace("out-of-distribution", "outofdistribution")
    text = text.replace("real-time", "realtime")
    text = text.replace("low-light", "lowlight")
    text = re.sub(r"[^a-z0-9\+\-\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokenize(text: str) -> List[str]:
    text = normalize_text(text)
    tokens = re.findall(r"[a-z0-9][a-z0-9\+\-]{1,}", text)
    cleaned = []
    for token in tokens:
        token = token.strip("-+")
        if not token or token in STOPWORDS:
            continue
        if token.isdigit():
            continue
        cleaned.append(token)
    return cleaned


def parse_list_cell(value: str) -> List[str]:
    return [html.unescape(item.strip()) for item in value.split(";") if item.strip()]


def parse_institution_cell(value: str) -> List[str]:
    items = []
    for raw in value.split(";"):
        item = normalize_institution(raw)
        if item:
            items.append(item)
    return items


def normalize_institution(value: str) -> str:
    value = html.unescape(value or "").strip()
    value = value.replace("&amp", "&")
    value = re.sub(r"Xi(?:&#x27|‘|')\s*", "Xi'an ", value)
    value = re.sub(r"xi(?:&#x27|‘|')\s*", "xi'an ", value)
    value = re.sub(r"\s+", " ", value)
    if not value:
        return ""
    if value.lower().strip(" :") in {"xi", "xi'an"}:
        return ""
    comma_parts = [part.strip() for part in value.split(",") if part.strip()]
    if len(comma_parts) >= 2 and len(set(comma_parts)) == 1:
        value = comma_parts[0]
    return value


def generate_doc_terms(title_tokens: Sequence[str], abstract_tokens: Sequence[str]) -> Counter:
    counts: Counter = Counter()
    for token in title_tokens:
        counts[token] += 2
    for token in abstract_tokens:
        counts[token] += 1

    combined_title = list(title_tokens)
    combined_abstract = list(abstract_tokens)
    for n in (2, 3):
        for seq, bonus in ((combined_title, 3), (combined_abstract, 1)):
            if len(seq) < n:
                continue
            for idx in range(len(seq) - n + 1):
                gram = tuple(seq[idx: idx + n])
                if gram[0] in STOPWORDS or gram[-1] in STOPWORDS:
                    continue
                phrase = " ".join(gram)
                counts[phrase] += bonus
    return counts


def load_papers(csv_path: Path) -> List[Paper]:
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    papers: List[Paper] = []
    for row in rows:
        title = row["title"].strip()
        abstract = row["abstract"].strip()
        title_tokens = tokenize(title)
        abstract_tokens = tokenize(abstract)
        combined_text = f"{title} {abstract}".strip()
        all_tokens = title_tokens + abstract_tokens
        doc_terms = generate_doc_terms(title_tokens, abstract_tokens)
        challenge_hit = any(token in CHALLENGE_TERMS for token in all_tokens)
        papers.append(
            Paper(
                paper_id=row["id"],
                title=title,
                abstract=abstract,
                authors=parse_list_cell(row["authors"]),
                institutions=parse_institution_cell(row["institutions"]),
                decision=row["decision"].strip(),
                eventtype=row["eventtype"].strip(),
                combined_text=combined_text,
                tokens=all_tokens,
                title_tokens=title_tokens,
                doc_terms=doc_terms,
                challenge_hit=challenge_hit,
            )
        )
    return papers


def count_hot_phrases(papers: Sequence[Paper]) -> List[Dict]:
    doc_freq: Counter = Counter()
    oral_freq: Counter = Counter()
    title_hits: Counter = Counter()
    sample_titles: Dict[str, List[str]] = defaultdict(list)

    for paper in papers:
        seen = set()
        title_seen = set()
        for term in paper.doc_terms:
            seen.add(term)
        for n in (2, 3):
            if len(paper.title_tokens) >= n:
                for idx in range(len(paper.title_tokens) - n + 1):
                    title_seen.add(" ".join(paper.title_tokens[idx: idx + n]))

        for term in seen:
            if not is_candidate_phrase(term):
                continue
            doc_freq[term] += 1
            if paper.eventtype.lower() == "oral":
                oral_freq[term] += 1
            if len(sample_titles[term]) < 3:
                sample_titles[term].append(paper.title)
        for term in title_seen:
            if is_candidate_phrase(term):
                title_hits[term] += 1

    results = []
    for term, count in doc_freq.items():
        tokens = term.split()
        if len(tokens) < 2:
            continue
        if count < 6:
            continue
        if title_hits[term] < 3:
            continue
        if not any(token in TREND_TOKENS for token in tokens):
            continue
        oral = oral_freq[term]
        title_ratio = title_hits[term] / count
        oral_ratio = oral / count
        trend_bonus = 1.15 if any(token in TREND_TOKENS for token in tokens) else 1.0
        score = count * (1.0 + 1.2 * oral_ratio + 1.1 * title_ratio) * trend_bonus
        results.append(
            {
                "phrase": term,
                "paper_count": count,
                "oral_count": oral,
                "oral_ratio": round(oral_ratio, 4),
                "title_hits": title_hits[term],
                "score": round(score, 3),
                "sample_titles": " | ".join(sample_titles[term]),
            }
        )
    results.sort(key=lambda item: (item["score"], item["paper_count"]), reverse=True)
    return results


def is_candidate_phrase(term: str) -> bool:
    tokens = term.split()
    if len(tokens) == 1:
        token = tokens[0]
        return (
            len(token) > 2
            and token not in GENERIC_TERMS
            and token not in STOPWORDS
            and token not in RHETORICAL_TERMS
        )
    if any(token in RHETORICAL_TERMS for token in tokens):
        return False
    if any(token in STOPWORDS for token in tokens):
        return False
    if all(token in GENERIC_TERMS for token in tokens):
        return False
    if len(tokens) == 2 and tokens[0] == tokens[1]:
        return False
    return True


def build_feature_vocabulary(papers: Sequence[Paper], feature_size: int) -> List[str]:
    doc_freq: Counter = Counter()
    oral_bonus: Counter = Counter()
    for paper in papers:
        seen = set()
        for term in paper.doc_terms:
            if not is_feature_candidate(term):
                continue
            seen.add(term)
        for term in seen:
            doc_freq[term] += 1
            if paper.eventtype.lower() == "oral":
                oral_bonus[term] += 1

    scored = []
    for term, df in doc_freq.items():
        if df < 10:
            continue
        bonus = oral_bonus[term] / max(df, 1)
        term_size_bonus = 1.15 if " " in term else 1.0
        score = df * (1.0 + 0.7 * bonus) * term_size_bonus
        scored.append((score, term))

    scored.sort(reverse=True)
    return [term for _, term in scored[:feature_size]]


def is_feature_candidate(term: str) -> bool:
    tokens = term.split()
    if len(tokens) == 1:
        return len(tokens[0]) > 2 and tokens[0] not in STOPWORDS
    return is_candidate_phrase(term)


def build_tfidf_matrix(papers: Sequence[Paper], vocabulary: Sequence[str]) -> Tuple[np.ndarray, np.ndarray]:
    vocab_index = {term: idx for idx, term in enumerate(vocabulary)}
    n_docs = len(papers)
    n_features = len(vocabulary)
    matrix = np.zeros((n_docs, n_features), dtype=np.float32)
    doc_freq = np.zeros(n_features, dtype=np.float32)

    for i, paper in enumerate(papers):
        seen = set()
        for term, count in paper.doc_terms.items():
            idx = vocab_index.get(term)
            if idx is None:
                continue
            matrix[i, idx] = float(count)
            seen.add(idx)
        for idx in seen:
            doc_freq[idx] += 1.0

    idf = np.log((1.0 + n_docs) / (1.0 + doc_freq)) + 1.0
    matrix *= idf
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    matrix /= norms
    return matrix, idf


def kmeans_pp_init(matrix: np.ndarray, k: int, rng: random.Random) -> np.ndarray:
    n_docs = matrix.shape[0]
    indices = [rng.randrange(n_docs)]
    for _ in range(1, k):
        centroids = matrix[indices]
        distances = 1.0 - np.max(matrix @ centroids.T, axis=1)
        distances = np.clip(distances, 0.0, None)
        total = float(distances.sum())
        if total <= 0:
            indices.append(rng.randrange(n_docs))
            continue
        target = rng.random() * total
        cumulative = 0.0
        chosen = 0
        for idx, value in enumerate(distances):
            cumulative += float(value)
            if cumulative >= target:
                chosen = idx
                break
        indices.append(chosen)
    return matrix[indices].copy()


def run_kmeans(matrix: np.ndarray, k: int, seed: int, max_iter: int = 35) -> Tuple[np.ndarray, np.ndarray]:
    rng = random.Random(seed)
    centroids = kmeans_pp_init(matrix, k, rng)
    labels = np.zeros(matrix.shape[0], dtype=np.int32)

    for _ in range(max_iter):
        similarity = matrix @ centroids.T
        new_labels = similarity.argmax(axis=1).astype(np.int32)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for cluster_id in range(k):
            members = matrix[labels == cluster_id]
            if len(members) == 0:
                centroids[cluster_id] = matrix[rng.randrange(matrix.shape[0])]
                continue
            centroid = members.mean(axis=0)
            norm = np.linalg.norm(centroid)
            if norm > 0:
                centroid = centroid / norm
            centroids[cluster_id] = centroid
    return labels, centroids


def normalize_series(values: Sequence[float]) -> List[float]:
    if not values:
        return []
    low = min(values)
    high = max(values)
    if math.isclose(low, high):
        return [0.5 for _ in values]
    return [(value - low) / (high - low) for value in values]


def cluster_domain_span(top_terms: Sequence[str]) -> Tuple[int, List[str]]:
    hits = []
    blob = " ".join(top_terms)
    for domain_name, lexicon in DOMAIN_LEXICONS.items():
        if any(term in blob for term in lexicon):
            hits.append(domain_name)
    return len(hits), hits


def choose_descriptive_terms(vocabulary: Sequence[str], centroid: np.ndarray, limit: int = 8) -> List[str]:
    ranked_indices = centroid.argsort()[::-1]
    selected: List[str] = []
    fallback: List[str] = []
    for idx in ranked_indices:
        weight = float(centroid[idx])
        if weight <= 0:
            continue
        term = vocabulary[idx]
        tokens = term.split()
        fallback.append(term)
        if len(tokens) == 1 and (term in GENERIC_TERMS or term in RHETORICAL_TERMS or term in STOPWORDS):
            continue
        if len(tokens) == 1 and term not in TREND_TOKENS and weight < 0.12:
            continue
        selected.append(term)
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        for term in fallback:
            if term not in selected:
                selected.append(term)
            if len(selected) >= limit:
                break
    return selected[:limit]


def summarize_clusters(
    papers: Sequence[Paper],
    matrix: np.ndarray,
    labels: np.ndarray,
    centroids: np.ndarray,
    vocabulary: Sequence[str],
) -> List[Dict]:
    summaries: List[Dict] = []
    n_docs = len(papers)

    for cluster_id in range(centroids.shape[0]):
        member_indices = np.where(labels == cluster_id)[0]
        if len(member_indices) == 0:
            continue

        centroid = centroids[cluster_id]
        top_terms = choose_descriptive_terms(vocabulary, centroid, limit=8)
        label_name = " / ".join(top_terms[:3]) if top_terms else f"cluster_{cluster_id}"

        cluster_papers = [papers[idx] for idx in member_indices]
        oral_count = sum(1 for paper in cluster_papers if paper.eventtype.lower() == "oral")
        institution_set = {inst for paper in cluster_papers for inst in paper.institutions}
        challenge_ratio = sum(1 for paper in cluster_papers if paper.challenge_hit) / len(cluster_papers)
        oral_ratio = oral_count / len(cluster_papers)

        similarities = matrix[member_indices] @ centroid
        ranked_members = member_indices[np.argsort(similarities)[::-1]]
        representative_titles = [papers[idx].title for idx in ranked_members[:5]]

        domain_span, domains = cluster_domain_span(top_terms)
        share = len(cluster_papers) / n_docs
        size_mid_bonus = math.exp(-((share - 0.06) ** 2) / (2 * (0.035 ** 2)))

        summaries.append(
            {
                "cluster_id": int(cluster_id),
                "topic_label": label_name,
                "paper_count": int(len(cluster_papers)),
                "paper_share": round(share, 4),
                "oral_count": int(oral_count),
                "oral_ratio": round(oral_ratio, 4),
                "institution_count": int(len(institution_set)),
                "institution_diversity": len(institution_set) / len(cluster_papers),
                "challenge_ratio": round(challenge_ratio, 4),
                "domain_span": int(domain_span),
                "domains": ", ".join(domains),
                "size_mid_bonus": round(size_mid_bonus, 4),
                "top_terms": " | ".join(top_terms),
                "representative_papers": " | ".join(representative_titles),
            }
        )

    if not summaries:
        return summaries

    oral_norm = normalize_series([item["oral_ratio"] for item in summaries])
    inst_norm = normalize_series([item["institution_diversity"] for item in summaries])
    challenge_norm = normalize_series([item["challenge_ratio"] for item in summaries])
    span_norm = normalize_series([float(item["domain_span"]) for item in summaries])
    mid_norm = normalize_series([item["size_mid_bonus"] for item in summaries])

    for item, oral_score, inst_score, challenge_score, span_score, mid_score in zip(
        summaries, oral_norm, inst_norm, challenge_norm, span_norm, mid_norm
    ):
        frontier = (
            0.28 * oral_score
            + 0.22 * inst_score
            + 0.20 * challenge_score
            + 0.18 * span_score
            + 0.12 * mid_score
        )
        item["frontier_score"] = round(frontier * 100, 2)
        item["direction_reason"] = explain_direction(item)

    summaries.sort(key=lambda row: row["paper_count"], reverse=True)
    return summaries


def explain_direction(item: Dict) -> str:
    reasons = []
    if item["oral_ratio"] >= 0.05:
        reasons.append("oral占比较高")
    if item["institution_diversity"] >= 0.8:
        reasons.append("机构分布较广")
    if item["challenge_ratio"] >= 0.45:
        reasons.append("大量论文聚焦泛化/效率/鲁棒性难题")
    if item["domain_span"] >= 2:
        reasons.append("有明显交叉方向特征")
    if item["paper_share"] >= 0.08:
        reasons.append("已经形成成规模热点")
    elif item["paper_share"] <= 0.03:
        reasons.append("规模仍适中，适合切入")
    return "；".join(reasons[:3]) or "主题已形成一定讨论度"


def write_csv(path: Path, rows: Sequence[Dict], fieldnames: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def top_institutions(papers: Sequence[Paper], limit: int = 20) -> List[Tuple[str, int]]:
    counter: Counter = Counter()
    for paper in papers:
        seen = set(paper.institutions)
        for institution in seen:
            counter[institution] += 1
    return counter.most_common(limit)


def save_stats(path: Path, papers: Sequence[Paper], clusters: Sequence[Dict]) -> None:
    stats = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "paper_count": len(papers),
        "oral_count": sum(1 for paper in papers if paper.eventtype.lower() == "oral"),
        "poster_count": sum(1 for paper in papers if paper.eventtype.lower() == "poster"),
        "unique_institutions": len({inst for paper in papers for inst in paper.institutions}),
        "cluster_count": len(clusters),
    }
    path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")


def plot_hot_topics(path: Path, hotspot_rows: Sequence[Dict], top_n: int = 15) -> None:
    rows = list(hotspot_rows[:top_n])
    labels = [row["phrase"] for row in rows][::-1]
    values = [row["paper_count"] for row in rows][::-1]
    plt.figure(figsize=(12, 8))
    plt.barh(labels, values, color="#2f6f89")
    plt.xlabel("Paper Count")
    plt.title("CVPR 2026 Hot Topic Phrases")
    plt.tight_layout()
    plt.savefig(path, dpi=180)
    plt.close()


def plot_cluster_sizes(path: Path, cluster_rows: Sequence[Dict]) -> None:
    rows = sorted(cluster_rows, key=lambda item: item["paper_count"], reverse=True)
    labels = [f"C{row['cluster_id']}" for row in rows]
    sizes = [row["paper_count"] for row in rows]
    plt.figure(figsize=(12, 6))
    plt.bar(labels, sizes, color="#d98c3f")
    plt.ylabel("Paper Count")
    plt.title("CVPR 2026 Topic Cluster Sizes")
    plt.tight_layout()
    plt.savefig(path, dpi=180)
    plt.close()


def build_report(
    path: Path,
    papers: Sequence[Paper],
    hotspot_rows: Sequence[Dict],
    cluster_rows: Sequence[Dict],
    institution_rows: Sequence[Tuple[str, int]],
) -> None:
    top_hotspots = hotspot_rows[:10]
    promising = sorted(cluster_rows, key=lambda item: item["frontier_score"], reverse=True)[:8]
    largest = cluster_rows[:10]
    oral_count = sum(1 for paper in papers if paper.eventtype.lower() == "oral")
    poster_count = len(papers) - oral_count
    unique_institutions = len({inst for paper in papers for inst in paper.institutions})

    lines = [
        "# CVPR 2026 热点与潜在研究方向分析",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 论文总数：{len(papers)}",
        f"- Oral / Poster：{oral_count} / {poster_count}",
        f"- 去重机构数：{unique_institutions}",
        "",
        "## 一、数据解读方式",
        "",
        "- 热点：根据标题和摘要中的高频短语、oral占比、标题命中率综合排序。",
        "- 研究方向：基于轻量TF-IDF聚类，再结合oral占比、机构多样性、挑战词密度和交叉性做启发式打分。",
        "- 说明：这更适合做“今年的热点地图”和“今年值得切入的主题”，不等同于多年趋势预测。",
        "",
        "## 二、热点短语 Top 10",
        "",
    ]

    for idx, row in enumerate(top_hotspots, start=1):
        lines.append(
            f"{idx}. `{row['phrase']}`: {row['paper_count']}篇，oral占比 {row['oral_ratio']:.1%}。"
        )
    lines.extend(["", "## 三、潜在研究方向 Top 8", ""])

    for idx, row in enumerate(promising, start=1):
        lines.append(
            f"{idx}. `{row['topic_label']}`: frontier score {row['frontier_score']}，"
            f"规模 {row['paper_count']} 篇，oral占比 {row['oral_ratio']:.1%}，"
            f"机构数 {row['institution_count']}。{row['direction_reason']}。"
        )
        lines.append(f"代表论文：{row['representative_papers']}")
    lines.extend(["", "## 四、主要主题簇", ""])

    for row in largest:
        lines.append(
            f"- C{row['cluster_id']} `{row['topic_label']}`: {row['paper_count']}篇，"
            f"top terms = {row['top_terms']}"
        )
    lines.extend(["", "## 五、高频机构", ""])

    for idx, (institution, count) in enumerate(institution_rows[:15], start=1):
        lines.append(f"{idx}. {institution}: {count}")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    random.seed(args.seed)
    np.random.seed(args.seed)

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    papers = load_papers(args.input)
    hotspot_rows = count_hot_phrases(papers)
    vocabulary = build_feature_vocabulary(papers, args.feature_size)
    matrix, _idf = build_tfidf_matrix(papers, vocabulary)
    labels, centroids = run_kmeans(matrix, args.clusters, args.seed)
    cluster_rows = summarize_clusters(papers, matrix, labels, centroids, vocabulary)
    promising_rows = sorted(cluster_rows, key=lambda item: item["frontier_score"], reverse=True)
    institution_rows = top_institutions(papers, limit=25)

    hotspot_path = output_dir / "hot_topics.csv"
    clusters_path = output_dir / "topic_clusters.csv"
    promising_path = output_dir / "promising_directions.csv"
    report_path = output_dir / "analysis_report.md"
    stats_path = output_dir / "stats.json"

    write_csv(
        hotspot_path,
        hotspot_rows,
        ["phrase", "paper_count", "oral_count", "oral_ratio", "title_hits", "score", "sample_titles"],
    )
    write_csv(
        clusters_path,
        cluster_rows,
        [
            "cluster_id",
            "topic_label",
            "paper_count",
            "paper_share",
            "oral_count",
            "oral_ratio",
            "institution_count",
            "institution_diversity",
            "challenge_ratio",
            "domain_span",
            "domains",
            "size_mid_bonus",
            "frontier_score",
            "direction_reason",
            "top_terms",
            "representative_papers",
        ],
    )
    write_csv(
        promising_path,
        promising_rows,
        [
            "cluster_id",
            "topic_label",
            "frontier_score",
            "paper_count",
            "paper_share",
            "oral_ratio",
            "institution_count",
            "institution_diversity",
            "challenge_ratio",
            "domain_span",
            "domains",
            "direction_reason",
            "top_terms",
            "representative_papers",
        ],
    )
    save_stats(stats_path, papers, cluster_rows)
    plot_hot_topics(output_dir / "hot_topics.png", hotspot_rows)
    plot_cluster_sizes(output_dir / "topic_cluster_sizes.png", cluster_rows)
    build_report(report_path, papers, hotspot_rows, cluster_rows, institution_rows)

    print(f"Loaded papers: {len(papers)}")
    print(f"Vocabulary size: {len(vocabulary)}")
    print(f"Clusters: {len(cluster_rows)}")
    print(f"Hot topic CSV: {hotspot_path}")
    print(f"Promising directions CSV: {promising_path}")
    print(f"Markdown report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
