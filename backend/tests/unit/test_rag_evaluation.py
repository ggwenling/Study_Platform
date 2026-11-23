import asyncio
import copy
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from study_platform.cli import evaluate_rag
from study_platform.services.rag_evaluation import (
    format_retrieval_table,
    quality_gate,
    read_dataset,
    review_template,
    summarize_retrieval,
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
    report = {
        "cases": rows,
        "strategy": "compare",
        "status": "completed",
        "expected_case_ids": ["answerable", "no-answer"],
    }
    review = review_template(report)
    review.update(reviewer="test reviewer", human_confirmed=True)
    for row in review["cases"]:
        row["fact_correct"] = row["id"] != "answerable" or row["strategy"] == "rewrite"
        row["citations_supported"] = True
    return report, review


@pytest.mark.parametrize(
    "damage", ["interrupted", "missing_question", "missing_side", "duplicate", "legacy", "single"]
)
def test_incomplete_comparisons_never_pass_even_with_all_existing_rows_reviewed(damage):
    report, _ = comparison()
    if damage == "interrupted":
        report["status"] = "running"
    elif damage == "missing_question":
        report["expected_case_ids"].append("second-paraphrase")
    elif damage == "missing_side":
        report["cases"].pop()
    elif damage == "duplicate":
        report["cases"].append(report["cases"][0])
    elif damage == "legacy":
        del report["expected_case_ids"]
    else:
        report["strategy"] = "baseline"
    review = review_template(report)
    review.update(reviewer="Reviewer", human_confirmed=True)
    review["cases"] = list({(r["id"], r["strategy"]): r for r in review["cases"]}.values())
    for row in review["cases"]:
        row.update(fact_correct=True, citations_supported=True)
    gate = quality_gate(report, review)
    assert not gate["complete_comparison"] and not gate["eligible_for_default"]


def test_interrupted_live_evaluation_keeps_planned_questions_and_running_status(
    tmp_path, monkeypatch
):
    dataset = tmp_path / "facts.jsonl"
    dataset.write_text("test", encoding="utf-8")
    args = SimpleNamespace(
        strategy="compare",
        dataset=dataset,
        retrieval_only=False,
        metric_k=[1, 3, 6, 10],
        metrics_format="table",
    )
    cases = [{"id": n, "knowledge_base": "kb"} for n in ("first", "second")]
    monkeypatch.setattr(evaluate_rag, "isolated_database", lambda _: nullcontext(None))
    monkeypatch.setattr(
        evaluate_rag, "seed_corpus", lambda *_: ({"kb": {}}, {}, {}, [], uuid4())
    )

    async def eval_case(case, *_args, **_kwargs):
        if case["id"] == "second":
            raise RuntimeError("interrupted")
        return [{"id": "first", "strategy": name} for name in ("baseline", "rewrite")]

    monkeypatch.setattr(evaluate_rag, "evaluate_case", eval_case)
    with pytest.raises(RuntimeError, match="interrupted"):
        asyncio.run(evaluate_rag.live_evaluation(args, cases, [], tmp_path))
    saved = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert saved["status"] == "running" and saved["expected_case_ids"] == ["first", "second"]
    assert [r["id"] for r in saved["cases"]] == ["first", "first"]


@pytest.mark.parametrize("quote", ["……", ["有效依据", "。？！"]])
def test_dataset_rejects_quotes_that_normalize_to_empty(tmp_path, quote):
    source = Path(__file__).resolve().parents[2] / "evaluation/facts.jsonl"
    cases, materials = read_dataset(source)
    case = cases[0]
    case["support"][0]["quote"] = quote
    path = tmp_path / "facts.jsonl"
    path.write_text(json.dumps(case), encoding="utf-8")
    path.with_name("materials.json").write_text(json.dumps(materials), encoding="utf-8")
    with pytest.raises(ValueError, match="supporting passages"):
        read_dataset(path)
    case["support"][0]["quote"] = "……"
    document = case["support"][0]["document"]
    assert not support_hit(
        case, [{"material_version_id": "v", "text": "任意内容"}], {"v": document}
    )


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


def test_retrieval_mini_covers_semantic_blocks_and_scanned_page():
    path = Path(__file__).resolve().parents[2] / "evaluation/retrieval_mini.jsonl"
    cases, materials = read_dataset(path)

    assert {case["id"] for case in cases} >= {"M04", "M05", "M06", "M07"}
    by_id = {material["id"]: material for material in materials}
    assert by_id["semantic-structures"]["format"] == "docx"
    assert by_id["scan-watermark"]["format"] == "pdf"
    structures = "\n".join(by_id["semantic-structures"]["sections"])
    assert "```python" in structures and "| --- | --- |" in structures and "$$" in structures
    assert "水印" in by_id["scan-watermark"]["sections"][0]


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


def test_retrieval_summary_from_mock_report():
    report = {
        "cases": [
            {
                "knowledge_base": "A",
                "retrieval_ir": {
                    "skipped": False,
                    "mrr": 1.0,
                    "recall@1": 1.0,
                    "recall@3": 1.0,
                    "ndcg@1": 1.0,
                    "ndcg@3": 1.0,
                    "hit@1": 1.0,
                    "hit@3": 1.0,
                },
            },
            {
                "knowledge_base": "A",
                "retrieval_ir": {
                    "skipped": True,
                    "mrr": 0.0,
                    "recall@1": 0.0,
                    "recall@3": 0.0,
                    "ndcg@1": 0.0,
                    "ndcg@3": 0.0,
                    "hit@1": 0.0,
                    "hit@3": 0.0,
                },
            },
            {
                "knowledge_base": "B",
                "retrieval_ir": {
                    "skipped": False,
                    "mrr": 0.5,
                    "recall@1": 0.0,
                    "recall@3": 1.0,
                    "ndcg@1": 0.0,
                    "ndcg@3": 0.631,
                    "hit@1": 0.0,
                    "hit@3": 1.0,
                },
            },
        ]
    }
    summary = summarize_retrieval(report, [1, 3])
    assert summary["by_scope"]["overall"]["n_scored"] == 2
    assert summary["by_scope"]["A"]["mrr"] == 1.0
    table = format_retrieval_table(summary)
    assert "overall" in table and "R@1" in table


def test_blank_evidence_uses_product_pack_and_does_not_call_the_model(monkeypatch):
    from study_platform.services import chat
    from study_platform.services.rag_evaluation import evaluate_case

    calls = {"model": 0}

    def embed(queries, diagnostics=None):
        if diagnostics is not None:
            diagnostics["calls"] = 1
            diagnostics["input_count"] = len(queries)
        return [[0.0]]

    def retrieve(_scope, _vectors, _owner):
        return [
            {
                "id": "blank",
                "text": " \n\t ",
                "material_version_id": "m",
                "index_version_id": "idx",
                "title": "t",
                "locator": "L1",
            }
        ]

    class Model:
        async def ainvoke(self, messages):
            calls["model"] += 1
            return SimpleNamespace(content="alpha [S1]", usage_metadata=None)

    monkeypatch.setattr("study_platform.services.rag_evaluation.embed_texts", embed)
    monkeypatch.setattr(
        "study_platform.services.rag_evaluation.chat.retrieve_vectors", retrieve
    )
    monkeypatch.setattr("study_platform.services.rag_evaluation.chat_model", lambda: Model())
    case = {
        "id": "Q",
        "fact_group": "g",
        "split": "heldout",
        "question": "问什么",
        "knowledge_base": "kb",
        "answerable": True,
        "expected_facts": [["alpha"]],
        "support": [{"document": "doc", "quote": "alpha"}],
    }
    scope = {"index_version_ids": ["idx"], "answer_mode": "knowledge_only", "intent": "qa"}
    rows = asyncio.run(evaluate_case(case, scope, {"m": "doc"}, "baseline", owner="owner"))
    assert calls["model"] == 0
    assert rows[0]["answer_metrics"]["calls"] == 0
    assert rows[0]["answer"] == chat.INSUFFICIENT_MATERIAL
    assert rows[0]["evidence"][0]["text"] == " \n\t "
    assert rows[0]["retrieval_support_hit"] is False
    assert rows[0]["evidence_characters"] == len(" \n\t ")

    def retrieve_text(_scope, _vectors, _owner):
        return [
            {
                "id": "c1",
                "text": "alpha quote",
                "material_version_id": "m",
                "index_version_id": "idx",
                "title": "t",
                "locator": "L1",
            }
        ]

    monkeypatch.setattr(
        "study_platform.services.rag_evaluation.chat.retrieve_vectors", retrieve_text
    )
    composed = asyncio.run(evaluate_case(case, scope, {"m": "doc"}, "baseline", owner="owner"))
    assert calls["model"] == 1
    assert composed[0]["answer_metrics"]["calls"] == 1
    assert composed[0]["answer"] == "alpha [S1]"


def test_equivalent_supporting_passages_are_accepted_but_file_match_alone_is_not():
    case = {"support": [{"document": "doc", "quote": ["按学期进行范围分区", "按授课学期进行分区"]}]}
    documents = {"v": "doc"}
    assert support_hit(
        case, [{"material_version_id": "v", "text": "成绩表按授课学期进行分区。"}], documents
    )
    assert not support_hit(
        case, [{"material_version_id": "v", "text": "本学期课程安排。"}], documents
    )
