"""Pure retrieval IR metrics on ranked document (or chunk) identifiers."""

import math
from collections.abc import Sequence


def unique_preserve_order(ids: Sequence[str]) -> list[str]:
    """Keep first occurrence when the ranker returns duplicate IDs."""
    seen: set[str] = set()
    ordered: list[str] = []
    for item in ids:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def recall_at_k(retrieved_ids: Sequence[str], expected_ids: set[str], k: int) -> float:
    if not expected_ids:
        return 0.0
    if k <= 0:
        return 0.0
    top = set(unique_preserve_order(retrieved_ids)[:k])
    return len(top & expected_ids) / len(expected_ids)


def hit_at_k(retrieved_ids: Sequence[str], expected_ids: set[str], k: int) -> float:
    if not expected_ids or k <= 0:
        return 0.0
    top = set(unique_preserve_order(retrieved_ids)[:k])
    return 1.0 if top & expected_ids else 0.0


def mrr(retrieved_ids: Sequence[str], expected_ids: set[str]) -> float:
    if not expected_ids:
        return 0.0
    for rank, doc_id in enumerate(unique_preserve_order(retrieved_ids), start=1):
        if doc_id in expected_ids:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved_ids: Sequence[str], expected_ids: set[str], k: int) -> float:
    if not expected_ids or k <= 0:
        return 0.0
    top = unique_preserve_order(retrieved_ids)[:k]
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, doc_id in enumerate(top, start=1)
        if doc_id in expected_ids
    )
    ideal_hits = min(len(expected_ids), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


def evaluate_ranking(
    retrieved_ids: Sequence[str],
    expected_ids: set[str],
    k_values: Sequence[int],
) -> dict[str, float | bool]:
    """Per-query metrics. Non-answerable items should use an empty ``expected_ids`` set."""
    skipped = not expected_ids
    base = {"skipped": skipped, "mrr": 0.0 if skipped else mrr(retrieved_ids, expected_ids)}
    if skipped:
        for k in k_values:
            base[f"recall@{k}"] = 0.0
            base[f"ndcg@{k}"] = 0.0
            base[f"hit@{k}"] = 0.0
        return base
    for k in k_values:
        base[f"recall@{k}"] = recall_at_k(retrieved_ids, expected_ids, k)
        base[f"ndcg@{k}"] = ndcg_at_k(retrieved_ids, expected_ids, k)
        base[f"hit@{k}"] = hit_at_k(retrieved_ids, expected_ids, k)
    return base


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def aggregate_metric_rows(rows: list[dict], k_values: Sequence[int]) -> dict:
    """Macro-average metrics across scored (answerable) rows."""
    scored = [row for row in rows if not row.get("skipped")]
    summary: dict = {"n_total": len(rows), "n_scored": len(scored)}
    if not scored:
        summary["mrr"] = None
        for k in k_values:
            summary[f"recall@{k}"] = None
            summary[f"ndcg@{k}"] = None
            summary[f"hit@{k}"] = None
        return summary
    summary["mrr"] = _mean([row["mrr"] for row in scored])
    for k in k_values:
        summary[f"recall@{k}"] = _mean([row[f"recall@{k}"] for row in scored])
        summary[f"ndcg@{k}"] = _mean([row[f"ndcg@{k}"] for row in scored])
        summary[f"hit@{k}"] = _mean([row[f"hit@{k}"] for row in scored])
    return summary


def aggregate_by_knowledge_base(
    rows: list[dict],
    k_values: Sequence[int],
) -> dict:
    """Return ``overall`` plus one entry per knowledge base name."""
    groups: dict[str | None, list[dict]] = {}
    for row in rows:
        groups.setdefault(row.get("knowledge_base"), []).append(row)
    return {
        "overall": aggregate_metric_rows(rows, k_values),
        **{
            name: aggregate_metric_rows(group, k_values)
            for name, group in sorted(groups.items(), key=lambda item: item[0] or "")
            if name is not None
        },
    }