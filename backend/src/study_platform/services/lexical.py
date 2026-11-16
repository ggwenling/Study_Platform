"""Chinese-aware lexical indexing via jieba tokens and PostgreSQL simple FTS."""

import re
from functools import reduce

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


_MAX_QUERY_TERMS = 32
# Question words and particles that carry no retrieval signal. Matching them with
# OR semantics would let almost every chunk through the lexical branch.
QUERY_STOPWORDS = frozenset(
    "什么 多少 哪个 哪些 哪里 哪儿 怎么 怎样 如何 能否 可以 是否 请问 一下 为什么 几个 "
    "这个 那个 这些 那些 我们 他们 你们 还是 或者 以及 还有 如果 因为 所以 时候 "
    "the a an of to in on for and or is are was were what which who how".split()
)


def lexical_query_terms(query: str) -> list[str]:
    """Distinct informative tokens of a natural-language question.

    Single-character tokens (mostly particles like 的/是/在/哪) and question words
    are dropped; digits and Latin tokens of any length are kept.
    """
    terms: list[str] = []
    seen: set[str] = set()
    for token in _normalize_tokens(query or ""):
        folded = token.lower()
        if folded in seen or folded in QUERY_STOPWORDS:
            continue
        if len(token) == 1 and not token.isascii():
            continue
        seen.add(folded)
        terms.append(folded)
        if len(terms) >= _MAX_QUERY_TERMS:
            break
    return terms


def plain_tsquery(query: str):
    """OR-combined tsquery for a question.

    plainto_tsquery ANDs every token, so a full question (including particles such
    as 的/在/哪) almost never matches any chunk and the lexical branch silently
    returns nothing. We OR the informative terms and let ts_rank_cd order chunks by
    how many of them they cover.
    """
    terms = lexical_query_terms(query)
    if not terms:
        return None
    parts = [sa.func.plainto_tsquery("simple", term) for term in terms]
    return reduce(lambda left, right: left.op("||")(right), parts)


def lexical_column_values(text: str) -> dict:
    indexed = lexical_index_text(text)
    if not indexed:
        return {"lexical": None}
    return {"lexical": sa.func.to_tsvector("simple", indexed)}