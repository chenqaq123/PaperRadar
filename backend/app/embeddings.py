from __future__ import annotations

import hashlib
import json
import math
import os
import re
from functools import lru_cache
from typing import Iterable, Sequence


FALLBACK_DIM = int(os.environ.get("PAPER_RADAR_FALLBACK_EMBED_DIM", "384"))


def tokenize(text: str) -> list[str]:
    normalized = text.lower()
    normalized = normalized.replace("text-to-image", "text_to_image")
    normalized = normalized.replace("vision-language", "vision_language")
    return re.findall(r"[a-z0-9_][a-z0-9_\-]{1,}", normalized)


def normalize_vector(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vec))
    if norm == 0:
        return [0.0 for _ in vec]
    return [float(value / norm) for value in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    return float(sum(x * y for x, y in zip(a, b)))


def mean_embedding(vectors: Sequence[Sequence[float]]) -> list[float]:
    vectors = [vec for vec in vectors if vec]
    if not vectors:
        return [0.0] * FALLBACK_DIM
    dim = len(vectors[0])
    summed = [0.0] * dim
    for vec in vectors:
        for idx, value in enumerate(vec[:dim]):
            summed[idx] += value
    return normalize_vector([value / len(vectors) for value in summed])


def dumps_embedding(vec: Sequence[float]) -> str:
    return json.dumps([round(float(value), 7) for value in vec], separators=(",", ":"))


def loads_embedding(raw: str | None) -> list[float]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [float(value) for value in data]


def _fallback_embed(text: str, dim: int = FALLBACK_DIM) -> list[float]:
    vec = [0.0] * dim
    tokens = tokenize(text)
    if not tokens:
        return vec
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        weight = 1.0 + min(len(token), 20) / 20.0
        vec[bucket] += sign * weight
    return normalize_vector(vec)


@lru_cache(maxsize=1)
def _load_sentence_transformer():
    model_name = os.environ.get("PAPER_RADAR_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except Exception:
        return None
    try:
        return SentenceTransformer(model_name, local_files_only=True)
    except TypeError:
        pass
    except Exception:
        pass
    try:
        return SentenceTransformer(model_name)
    except Exception:
        return None


def embed_texts(texts: Iterable[str]) -> list[list[float]]:
    texts = [text or "" for text in texts]
    backend = os.environ.get("PAPER_RADAR_EMBED_BACKEND", "auto").lower()
    if backend != "fallback":
        model = _load_sentence_transformer()
        if model is not None:
            vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
            return [[float(value) for value in vector] for vector in vectors]
    return [_fallback_embed(text) for text in texts]
