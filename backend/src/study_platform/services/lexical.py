"""Chinese-aware lexical indexing via jieba tokens and PostgreSQL simple FTS."""

import re

import jieba
import sqlalchemy as sa

_MAX_LEXICAL_CHARS = 1_000_000
_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _normalize_tokens(text: str) -> list[str]:
    if not text or not text.strip():
        return []
    if len(text) > _MAX_LEXICAL_CHARS:
        text = text[:_MAX_LEXICAL_CHARS]
    tokens: list[str] = []
    for piece in jieba.cut_for_search(text):
        piece = piece.strip()
        if not piece or not _TOKEN_RE.search(piece):
            continue
        tokens.append(piece)
    return tokens


def lexical_index_text(text: str) -> str:
    """Space-joined tokens for to_tsvector('simple', ...)."""
    return " ".join(_normalize_tokens(text))


def plain_tsquery(query: str):
    indexed = lexical_index_text(query)
    if not indexed:
        return None
    return sa.func.plainto_tsquery("simple", indexed)


def lexical_column_values(text: str) -> dict:
    indexed = lexical_index_text(text)
    if not indexed:
        return {"lexical": None}
    return {"lexical": sa.func.to_tsvector("simple", indexed)}