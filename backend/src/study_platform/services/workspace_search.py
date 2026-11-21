"""Helpers for ranked workspace search."""

import base64
import binascii
import json
import math
import re
from uuid import UUID

import sqlalchemy as sa


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def title_search_expressions(column, query: str, terms: list[str]):
    escaped_query = _escape_like(query)
    exact = sa.func.lower(column) == query.lower()
    prefix = column.ilike(f"{escaped_query}%", escape="\\")
    substring = column.ilike(f"%{escaped_query}%", escape="\\")
    term_matches = [column.ilike(f"%{_escape_like(term)}%", escape="\\") for term in terms]
    coverage = sum(
        (sa.case((matched, 1.0), else_=0.0) for matched in term_matches),
        start=sa.literal(0.0),
    ) / len(terms) if terms else sa.literal(0.0)
    matched = sa.or_(exact, prefix, substring, *term_matches)
    score = sa.case(
        (exact, 100.0),
        (prefix, 70.0),
        (substring, 55.0),
        else_=coverage * 40.0,
    )
    return matched, score


def body_search_expression(text_column, lexical_column, query: str, tsquery):
    substring = text_column.ilike(f"%{_escape_like(query)}%", escape="\\")
    if tsquery is None:
        return substring
    if len(query) > 3:
        # Offline migration cannot run jieba, so historical rows may lack a vector.
        substring = sa.and_(lexical_column.is_(None), substring)
    return sa.or_(lexical_column.op("@@")(tsquery), substring)


def body_score_expression(lexical_column, terms: list[str], tsquery):
    if not terms or tsquery is None:
        return sa.literal(0.0)
    term_matches = [
        lexical_column.op("@@")(sa.func.plainto_tsquery("simple", term))
        for term in terms
    ]
    coverage = sum(
        (sa.case((matched, 1.0), else_=0.0) for matched in term_matches),
        start=sa.literal(0.0),
    ) / len(terms)
    rank = sa.func.coalesce(sa.func.ts_rank_cd(lexical_column, tsquery, 32), 0.0)
    return rank * 15.0 + coverage * 20.0


def search_excerpt(text: str, query: str, terms: list[str]) -> str:
    max_length = 120
    search_terms = terms or ([query.strip()] if query.strip() else [])
    patterns = [re.compile(re.escape(term), re.IGNORECASE) for term in search_terms if term]
    matches = sorted(
        (match.start(), match.end(), term_index)
        for term_index, pattern in enumerate(patterns)
        for match in pattern.finditer(text)
    )
    if not matches:
        start = 0
    else:
        term_counts = [0] * len(patterns)
        left = 0
        covered_terms = 0
        best_coverage = -1
        start = 0
        max_start = max(0, len(text) - max_length)
        for position, match_end, term_index in matches:
            match_end = min(match_end, position + max_length)
            if term_counts[term_index] == 0:
                covered_terms += 1
            term_counts[term_index] += 1
            while match_end - matches[left][0] > max_length:
                left_term = matches[left][2]
                term_counts[left_term] -= 1
                if term_counts[left_term] == 0:
                    covered_terms -= 1
                left += 1
            if covered_terms > best_coverage:
                best_coverage = covered_terms
                first_position = matches[left][0]
                context = min(45, (max_length - (match_end - first_position)) // 2)
                start = max(0, min(max_start, first_position - context))
    end = min(len(text), start + max_length)
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")


def matched_search_terms(title: str, text: str | None, query: str, terms: list[str]) -> list[str]:
    search_terms = terms or ([query.strip()] if query.strip() else [])
    haystack = f"{title}\n{text or ''}"
    return [term for term in search_terms if term and re.search(re.escape(term), haystack, re.IGNORECASE)]


def encode_search_cursor(score: float, type_rank: int, item_id: UUID) -> str:
    payload = json.dumps(
        [float(score).hex(), type_rank, str(item_id)], separators=(",", ":")
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def decode_search_cursor(cursor: str) -> tuple[float, int, UUID]:
    try:
        if len(cursor) > 256:
            raise ValueError
        raw = base64.b64decode(
            cursor.encode("ascii") + b"=" * (-len(cursor) % 4),
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(raw.decode("utf-8"))
        if (
            not isinstance(payload, list)
            or len(payload) != 3
            or not isinstance(payload[0], str)
            or not isinstance(payload[1], int)
            or isinstance(payload[1], bool)
            or payload[1] not in range(5)
            or not isinstance(payload[2], str)
        ):
            raise ValueError
        score = float.fromhex(payload[0])
        if not math.isfinite(score):
            raise ValueError
        return score, payload[1], UUID(payload[2])
    except (ValueError, OverflowError, binascii.Error) as exc:
        raise ValueError("Invalid search cursor") from exc
