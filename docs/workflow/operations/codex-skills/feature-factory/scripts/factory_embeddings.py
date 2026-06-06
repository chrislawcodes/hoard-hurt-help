"""Similarity helpers for judge concern matching.

The preferred path uses OpenAI embeddings when the `openai` package is
available and `OPENAI_API_KEY` is set. If that path is unavailable, the module
falls back to a degraded Jaccard signal over lowercase tokens with a small
stopword filter. The fallback is intentionally weaker and is logged once.
"""

from __future__ import annotations

import logging
import math
import os
import re

_LOGGER = logging.getLogger(__name__)
_FALLBACK_LOGGED = False

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a",
    "about",
    "after",
    "all",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "if",
    "in",
    "into",
    "is",
    "it",
    "no",
    "not",
    "of",
    "on",
    "or",
    "our",
    "so",
    "that",
    "the",
    "their",
    "then",
    "there",
    "these",
    "they",
    "this",
    "to",
    "was",
    "we",
    "with",
    "you",
}


def _log_fallback_once() -> None:
    global _FALLBACK_LOGGED
    if not _FALLBACK_LOGGED:
        _LOGGER.info("OpenAI embeddings unavailable; using degraded Jaccard similarity fallback")
        _FALLBACK_LOGGED = True


def _tokenize(text: str) -> set[str]:
    tokens = _TOKEN_RE.findall(text.lower())
    return {token for token in tokens if token not in _STOPWORDS}


def _jaccard_similarity(text_a: str, text_b: str) -> float:
    tokens_a = _tokenize(text_a)
    tokens_b = _tokenize(text_b)
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    union = tokens_a | tokens_b
    if not union:
        return 0.0
    return len(tokens_a & tokens_b) / len(union)


def _cosine_from_vectors(vector_a: list[float], vector_b: list[float]) -> float:
    if len(vector_a) != len(vector_b) or not vector_a:
        return 0.0
    dot = sum(a * b for a, b in zip(vector_a, vector_b))
    norm_a = math.sqrt(sum(a * a for a in vector_a))
    norm_b = math.sqrt(sum(b * b for b in vector_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    score = dot / (norm_a * norm_b)
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


def _openai_embedding(text: str) -> list[float] | None:
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        from openai import OpenAI
    except Exception:
        return None
    try:
        client = OpenAI()
        response = client.embeddings.create(model="text-embedding-3-small", input=text)
        data = getattr(response, "data", None)
        if not data:
            return None
        embedding = getattr(data[0], "embedding", None)
        if not isinstance(embedding, list):
            return None
        return [float(value) for value in embedding]
    except Exception:
        return None


def cosine_similarity(text_a: str, text_b: str) -> float:
    """Return a similarity score in the range [0.0, 1.0]."""
    vector_a = _openai_embedding(text_a)
    vector_b = _openai_embedding(text_b)
    if vector_a is not None and vector_b is not None:
        return _cosine_from_vectors(vector_a, vector_b)
    _log_fallback_once()
    return _jaccard_similarity(text_a, text_b)
