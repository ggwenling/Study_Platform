import copy
import json
from pathlib import Path

import pytest

from study_platform.cli import evaluate_rag
from study_platform.services.rag_evaluation import (
    quality_gate,
    read_dataset,
    review_template,
    support_hit,
)


def comparison():
    rows = []
    for key, answerable in [("answerable", True), ("no-answer", False)]:
        for strategy in ("baseline", "rewrite"):
            rows.append(
                {
                    "id": key,
                    "fact_group": key,
                    "split": "heldout",
                    "strategy": strategy,
                    "answerable": answerable,
                    "retrieval_support_hit": answerable and strategy == "rewrite",
                    "answer_error": None,
                    "citation_valid": True,
                    "scope_valid": True,
                }
            )
    report = {"cases": rows}
    review = review_template(report)
    review.update(reviewer="test reviewer", human_confirmed=True)
    for row in review["cases"]:
        row["fact_correct"] = row["id"] != "answerable" or row["strategy"] == "rewrite"
        row["citations_supported"] = True
    return report, review


def test_group_gain_requires_review_and_safety_without_regression():
    report, review = comparison()
    assert not quality_gate(report)["eligible_for_default"]
    assert quality_gate(report, review)["eligible_for_default"]
    review["cases"][-1]["fact_correct"] = False
    assert not quality_gate(report, review)["eligible_for_default"]


def test_filled_suggestions_without_human_confirmation_cannot_enable_default():
    report, review = comparison()
    review["human_confirmed"] = False
    gate = quality_gate(report, review)
    assert gate["pending_review"] and not gate["eligible_for_default"]


def test_review_is_bound_to_exact_report_and_case_ids():
    report, review = comparison()
    changed = copy.deepcopy(report)
    changed["cases"][0]["answer_error"] = "changed"
    with pytest.raises(ValueError, match="another report"):
        quality_gate(changed, review)
    review["cases"].append(review["cases"][0])
    with pytest.raises(ValueError, match="exactly"):
        quality_gate(report, review)


@pytest.mark.parametrize(
    "field,value",
    [("citation_valid", False), ("scope_valid", False), ("answer_error", "provider_failure")],
)
def test_invalid_citations_scope_or_model_errors_block_promotion(field, value):
    report, _ = comparison()
    report["cases"][1][field] = value
    review = review_template(report)
    review.update(reviewer="test reviewer", human_confirmed=True)
    for row in review["cases"]:
        row.update(fact_correct=True, citations_supported=True)
    assert not quality_gate(report, review)["eligible_for_default"]


def test_correct_baseline_answer_and_fact_group_must_not_regress():
    report, _ = comparison()
    report["cases"][0]["retrieval_support_hit"] = True
    report["cases"][1]["retrieval_support_hit"] = False
    review = review_template(report)
    review.update(reviewer="test reviewer", human_confirmed=True)
    for row in review["cases"]:
        row.update(fact_correct=True, citations_supported=True)
    review["cases"][1]["fact_correct"] = False
    gate = quality_gate(report, review)
    assert gate["heldout_lost_groups"] == ["answerable"]
    assert gate["answer_regressions"] == ["answerable"]
    assert not gate["eligible_for_default"]


def test_frozen_dataset_groups_and_splits_are_balanced():
    path = Path(__file__).resolve().parents[2] / "evaluation/facts.jsonl"
    cases, _ = read_dataset(path)
    assert len(cases) == 48 and len({case["fact_group"] for case in cases}) == 24
    for split in ("calibration", "heldout"):
        subset = [case for case in cases if case["split"] == split]
        assert len(subset) == 24
        assert sum(case["answerable"] for case in subset) == 16


def test_paraphrases_cannot_leak_across_splits(tmp_path):
    source = Path(__file__).resolve().parents[2] / "evaluation/facts.jsonl"
    cases, materials = read_dataset(source)
    cases[1]["split"] = "heldout"
    path = tmp_path / "facts.jsonl"
    path.write_text("\n".join(json.dumps(case) for case in cases), encoding="utf-8")
    path.with_name("materials.json").write_text(json.dumps(materials), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot cross"):
        read_dataset(path)


@pytest.mark.parametrize(
    "url",
    [
        None,
        "sqlite:///test_test",
        "postgresql://localhost/main",
        "postgresql://localhost/x_test?dbname=main",
        "postgresql://localhost/x_test?service=main",
    ],
)
def test_evaluation_rejects_unsafe_database_targets(url):
    with pytest.raises(ValueError):
        evaluate_rag.test_database_url(url)


def test_default_cli_validates_without_database_or_provider_calls(monkeypatch, capsys):
    def forbidden(*_, **__):
        raise AssertionError("dry run must not connect or call a provider")

    monkeypatch.setattr(evaluate_rag, "isolated_database", forbidden)
    monkeypatch.setattr(evaluate_rag, "embed_texts", forbidden)
    monkeypatch.setattr("sys.argv", ["study-eval-rag"])
    evaluate_rag.main()
    assert "No database or provider calls" in capsys.readouterr().out


def test_equivalent_supporting_passages_are_accepted_but_file_match_alone_is_not():
    case = {"support": [{"document": "doc", "quote": ["按学期进行范围分区", "按授课学期进行分区"]}]}
    documents = {"v": "doc"}
    assert support_hit(
        case, [{"material_version_id": "v", "text": "成绩表按授课学期进行分区。"}], documents
    )
    assert not support_hit(
        case, [{"material_version_id": "v", "text": "本学期课程安排。"}], documents
    )
