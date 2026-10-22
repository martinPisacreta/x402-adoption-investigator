"""Deterministic FNV-1a 64-dim hash embedder and cosine (port of engines/hmem/reference/src/embeddings.ts).

Pure function of the text, so retrieval scores are reproducible across runs and machines. No provider.
"""
from __future__ import annotations

import math
import re
from typing import Iterable

DIM = 64
_NON_ALNUM = re.compile(r"[^a-z0-9\s]")


def tokenize(text: str) -> list[str]:
    cleaned = _NON_ALNUM.sub(" ", text.lower())
    return [t for t in cleaned.split() if len(t) > 1]


def fnv1a(text: str) -> int:
    h = 0x811C9DC5
    for ch in text:
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    for token in tokenize(text):
        h = fnv1a(token)
        idx = h % DIM
        sign = 1.0 if ((h >> 8) % 2 == 0) else -1.0
        mag = 1 + ((h >> 16) % 8) / 8
        vec[idx] += sign * mag
    norm = math.sqrt(sum(v * v for v in vec))
    return vec if norm == 0 else [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))   # both vectors are unit length


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    return inter / (len(sa) + len(sb) - inter)
