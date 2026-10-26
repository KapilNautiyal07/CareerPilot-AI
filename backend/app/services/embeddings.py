"""Embeddings for semantic retrieval.

`hash` is deterministic feature hashing (signed, L2-normalized) over word tokens plus canonical
skill tokens. It needs no network or API key, which keeps tests reproducible. The interface is
deliberately tiny so a hosted embedding model can be dropped in via EMBEDDING_PROVIDER.
"""

from __future__ import annotations

import hashlib
import math
import re

from app.core.config import get_settings
from app.services import skills as tax

_TOKEN = re.compile(r"[a-z0-9+#.]{2,}")
_STOP = {
    "and",
    "the",
    "for",
    "with",
    "you",
    "our",
    "are",
    "will",
    "this",
    "that",
    "from",
    "your",
    "have",
    "of",
    "to",
    "in",
    "on",
    "a",
    "an",
    "is",
    "be",
    "as",
    "or",
    "at",
    "by",
    "it",
    "we",
}


def _tokens(text: str) -> list[tuple[str, float]]:
    toks: list[tuple[str, float]] = [(t, 1.0) for t in _TOKEN.findall(text.lower()) if t not in _STOP]
    # Canonical skills are strong signals and make synonyms ("postgres" / "PostgreSQL") collide.
    toks.extend((f"skill:{m.skill.lower()}", 3.0) for m in tax.find_skills(text, require_context_for_ambiguous=False))
    return toks


def embed(text: str, dim: int | None = None) -> list[float]:
    dim = dim or get_settings().embedding_dim
    vec = [0.0] * dim
    for tok, w in _tokens(text):
        h = hashlib.blake2b(tok.encode(), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "little") % dim
        sign = 1.0 if h[4] & 1 else -1.0
        vec[idx] += sign * w
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))
