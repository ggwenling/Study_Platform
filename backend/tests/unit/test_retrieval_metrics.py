import math

import pytest

from study_platform.services.retrieval_metrics import (
    aggregate_by_knowledge_base,
    evaluate_ranking,
    hit_at_k,
    mrr,
    ndcg_at_k,
    recall_at_k,
    unique_preserve_order,
)


def test_recall_and_hit_hand_calculated():
    retrieved = ["d2", "d1", "d3"]
    expected = {"d1", "d3"}
    assert recall_at_k(retrieved, expected, 1) == 0.0
    assert recall_at_k(retrieved, expected, 2) == 0.5
    assert recall_at_k(retrieved, expected, 3) == 1.0
    assert hit_at_k(retrieved, expected, 1) == 0.0
    assert hit_at_k(retrieved, expected, 2) == 1.0


def test_mrr_first_relevant_rank():
    assert mrr(["x", "d1", "d1"], {"d1"}) == 0.5
    assert mrr(["x", "y"], {"d1"}) == 0.0


def test_ndcg_hand_calculated():
    retrieved = ["d2", "d1"]
    expected = {"d1"}
    dcg = 1.0 / math.log2(3)
    idcg = 1.0 / math.log2(2)
    assert ndcg_at_k(retrieved, expected, 2) == pytest.approx(dcg / idcg)


def test_no_relevant_items():
    assert recall_at_k(["a"], set(), 5) == 0.0
    assert ndcg_at_k(["a"], set(), 5) == 0.0
    assert mrr(["a"], set()) == 0.0


def test_k_larger_than_result_length():
    assert recall_at_k(["d1"], {"d1"}, 10) == 1.0
    assert hit_at_k(["d1"], {"d1"}, 100) == 1.0
    assert ndcg_at_k(["d1"], {"d1"}, 10) == 1.0


def test_duplicate_results_deduped_for_recall():
    retrieved = ["d1", "d1", "d2"]
    expected = {"d1", "d2"}
    assert recall_at_k(retrieved, expected, 3) == 1.0
    assert unique_preserve_order(retrieved) == ["d1", "d2"]
    # MRR uses deduped ranks: second duplicate does not push the first hit down.
    assert mrr(retrieved, {"d1"}) == 1.0


def test_multiple_expected_documents():
    retrieved = ["d3", "d1", "d2"]
    expected = {"d1", "d2"}
    assert recall_at_k(retrieved, expected, 3) == 1.0
    assert recall_at_k(retrieved, expected, 2) == 0.5


def test_evaluate_ranking_skips_unanswerable():
    metrics = evaluate_ranking(["a"], set(), [1, 3])
    assert metrics["skipped"] is True
    assert metrics["mrr"] == 0.0


def test_aggregate_by_knowledge_base():
    k_values = [1, 3]
    rows = [
        {
            "knowledge_base": "A",
            "skipped": False,
            "mrr": 1.0,
            "recall@1": 1.0,
            "recall@3": 1.0,
            "ndcg@1": 1.0,
            "ndcg@3": 1.0,
            "hit@1": 1.0,
            "hit@3": 1.0,
        },
        {
            "knowledge_base": "B",
            "skipped": False,
            "mrr": 0.5,
            "recall@1": 0.0,
            "recall@3": 1.0,
            "ndcg@1": 0.0,
            "ndcg@3": 0.5,
            "hit@1": 0.0,
            "hit@3": 1.0,
        },
        {
            "knowledge_base": "A",
            "skipped": True,
            "mrr": 0.0,
            "recall@1": 0.0,
            "recall@3": 0.0,
            "ndcg@1": 0.0,
            "ndcg@3": 0.0,
            "hit@1": 0.0,
            "hit@3": 0.0,
        },
    ]
    grouped = aggregate_by_knowledge_base(rows, k_values)
    assert grouped["overall"]["n_scored"] == 2
    assert grouped["overall"]["mrr"] == pytest.approx(0.75)
    assert grouped["A"]["n_scored"] == 1
    assert grouped["B"]["recall@3"] == 1.0