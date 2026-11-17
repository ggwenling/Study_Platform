"""Document-aware ranking helpers used after vector and lexical retrieval."""

from __future__ import annotations

import math

# How many term-matching chunks the lexical branch rescoring looks at.
LEXICAL_IDF_POOL = 400


def idf_rank(rows: list, total_chunks: int) -> list:
    """Order lexical matches by the summed IDF of the query terms they contain.

    ``rows`` are ``(chunk, title, ts_rank, matched_flags)`` tuples where
    ``matched_flags[i]`` says whether query term ``i`` occurs in the chunk. Plain
    ``ts_rank_cd`` has no notion of term rarity, so a chunk matching common words
    (e.g. a year or a country name) outranks one matching the rare entity the
    question is about. Document frequency is taken over the matched pool; chunks
    outside it contain none of the terms, so it equals the in-scope frequency
    whenever the pool is not truncated.
    """
    if not rows:
        return []
    term_count = len(rows[0][3])
    total = max(total_chunks, len(rows))
    df = [sum(1 for row in rows if row[3][i]) for i in range(term_count)]
    idf = [math.log(1 + (total - d + 0.5) / (d + 0.5)) for d in df]
    scored = []
    for index, (chunk, title, ts_rank, flags) in enumerate(rows):
        score = sum(weight for weight, hit in zip(idf, flags) if hit)
        scored.append((-score, -(ts_rank or 0.0), index, chunk, title))
    scored.sort(key=lambda item: item[:3])
    return [(item[3], item[4]) for item in scored]


def fuse_rankings(rankings, limit, *, weights=None, rrf_k: int = 60):
    scores, rows, first_rank, best_rank = {}, {}, {}, {}
    branch_weights = weights or [1.0] * len(rankings)
    for branch, ranking in enumerate(rankings):
        weight = branch_weights[branch] if branch < len(branch_weights) else 1.0
        for rank, row in enumerate(ranking, 1):
            key = row["id"]
            scores[key] = scores.get(key, 0) + weight / (rrf_k + rank)
            rows[key] = row
            best_rank[key] = min(best_rank.get(key, rank), rank)
            if branch == 0 and key not in first_rank:
                first_rank[key] = rank
    ordered = sorted(
        rows,
        key=lambda key: (
            -scores[key],
            best_rank.get(key, float("inf")),
            first_rank.get(key, float("inf")),
            str(key),
        ),
    )
    return [rows[key] for key in ordered[:limit]]


def dedup_documents(chunks: list, limit: int, *, max_per_document: int = 1) -> list:
    """Diversify the head of the ranking by document, then backfill.

    The first pass keeps at most ``max_per_document`` chunks per material version so
    that near-duplicate chunks of one document cannot crowd other documents out of
    the top ranks. Remaining slots (if fewer distinct documents than ``limit`` were
    found) are backfilled with the skipped chunks in their original order, so a
    single-document knowledge base still gets several chunks of context.
    """
    if limit <= 0:
        return []
    kept: list = []
    skipped: list = []
    counts: dict = {}
    for chunk in chunks:
        key = chunk.get("material_version_id") or chunk.get("document") or chunk.get("id")
        seen = counts.get(key, 0)
        if seen >= max_per_document:
            skipped.append(chunk)
            continue
        counts[key] = seen + 1
        kept.append(chunk)
        if len(kept) >= limit:
            return kept
    return kept + skipped[: limit - len(kept)]
