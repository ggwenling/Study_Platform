import json

from study_platform.cli.evaluate_report_v2 import main
from study_platform.services.evaluation_v2 import (
    chunk_identity,
    cover_bounds,
    evaluate_paths,
    greedy_upper,
    packing_lower,
)
from study_platform.services.rag_evaluation import refusal_marker, support_hit
from study_platform.services.retrieval_metrics import evaluate_ranking


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _question(qid, support, *, answerable=True):
    return {
        "id": qid,
        "answerable": answerable,
        "question": f"question {qid}",
        "support": support,
    }


def _case(qid, evidence, *, strategy="baseline", answer="", stored_hit=None, retrieval_ir=None):
    row = {
        "id": qid,
        "strategy": strategy,
        "answer": answer,
        "evidence": evidence,
        "citations": [],
    }
    if stored_hit is not None:
        row["retrieval_support_hit"] = stored_hit
    if retrieval_ir is not None:
        row["retrieval_ir"] = retrieval_ir
    return row


def _run(tmp_path, questions, cases, *, manifest=None, annotations=None, top_k=10):
    tmp_path.mkdir(parents=True, exist_ok=True)
    report_path = tmp_path / "report.json"
    questions_path = tmp_path / "questions.jsonl"
    _write(report_path, {"top_k": top_k, "metric_k": [1, 3], "cases": cases})
    questions_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in questions),
        encoding="utf-8",
    )
    manifest_path = None
    if manifest is not None:
        manifest_path = tmp_path / "manifest.json"
        _write(manifest_path, manifest)
    annotation_path = None
    if annotations is not None:
        annotation_path = tmp_path / "annotations.jsonl"
        annotation_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in annotations),
            encoding="utf-8",
        )
    return evaluate_paths(
        report_path,
        questions_path,
        manifest_path=manifest_path,
        annotations_path=annotation_path,
    )


def test_refusal_marker_is_the_live_expression():
    assert refusal_marker("结论。资料不足。") is True
    assert refusal_marker("没有该标记的拒答") is False
    assert refusal_marker(None) is False


def test_cross_document_local_index_does_not_collide(tmp_path):
    questions = [
        _question(
            "Q",
            [
                {"document": "A", "quote": "alpha quote"},
                {"document": "B", "quote": "beta quote"},
            ],
        )
    ]
    manifest = {
        "complete": True,
        "chunks": [
            {"document": "A", "local_index": 0, "text": "alpha quote"},
            {"document": "B", "local_index": 0, "text": "beta quote"},
        ],
    }
    report = _run(tmp_path, questions, [_case("Q", [])], manifest=manifest, top_k=1)
    row = report["records"][0]["satisfiability"]
    assert chunk_identity(manifest["chunks"][0]) != chunk_identity(manifest["chunks"][1])
    assert row["algorithm"] == "exact"
    assert row["minimum"] == 2
    assert row["structural_at_k"] is False
    assert row["observed_covered"] is False
    assert row["scope"] == "corpus"


def test_same_chunk_covers_two_quotes_once(tmp_path):
    questions = [
        _question(
            "Q",
            [
                {"document": "A", "quote": "alpha quote"},
                {"document": "A", "quote": "beta quote"},
            ],
        )
    ]
    manifest = {
        "complete": True,
        "chunks": [{"document": "A", "local_index": 0, "text": "alpha quote beta quote"}],
    }
    report = _run(tmp_path, questions, [_case("Q", [])], manifest=manifest, top_k=1)
    assert report["records"][0]["satisfiability"]["minimum"] == 1
    assert report["records"][0]["satisfiability"]["structural_at_k"] is True


def test_identical_text_and_duplicate_rows_keep_document_identity(tmp_path):
    questions = [
        _question(
            "Q",
            [
                {"document": "A", "quote": "same words"},
                {"document": "B", "quote": "same words"},
            ],
        )
    ]
    manifest = {
        "complete": True,
        "chunks": [
            {"document": "A", "local_index": 0, "text": "same words"},
            {"document": "A", "local_index": 0, "text": "same words"},
            {"document": "B", "local_index": 0, "text": "same words"},
        ],
    }
    report = _run(tmp_path, questions, [_case("Q", [])], manifest=manifest, top_k=1)
    assert report["records"][0]["satisfiability"]["minimum"] == 2


def test_missing_document_identity_is_unknown_not_impossible(tmp_path):
    questions = [_question("Q", [{"document": "A", "quote": "alpha quote"}])]
    manifest = {"complete": True, "chunks": [{"local_index": 0, "text": "alpha quote"}]}
    report = _run(tmp_path, questions, [_case("Q", [])], manifest=manifest, top_k=10)
    row = report["records"][0]["satisfiability"]
    assert chunk_identity(manifest["chunks"][0]) is None
    assert row["algorithm"] == "unknown"
    assert row["structural_at_k"] == "unknown"
    assert row["candidate_complete"] is False
    assert row["universe"] == "incomplete_identity"
    assert "document-bound identity" in row["validation_error"]

    mixed = {
        "complete": True,
        "chunks": [
            {"document": "A", "local_index": 0, "text": "other text"},
            {"local_index": 1, "text": "alpha quote"},
        ],
    }
    withheld = _run(tmp_path / "mixed", questions, [_case("Q", [])], manifest=mixed, top_k=10)
    mixed_row = withheld["records"][0]["satisfiability"]
    assert mixed_row["structural_at_k"] == "unknown"
    assert mixed_row["minimum"] is None
    assert mixed_row["algorithm"] == "unknown"

    identified = {
        "complete": True,
        "chunks": [{"document": "A", "local_index": 0, "text": "other text"}],
    }
    absent = _run(tmp_path / "identified", questions, [_case("Q", [])], manifest=identified, top_k=10)
    assert absent["records"][0]["satisfiability"]["algorithm"] == "impossible"
    assert absent["records"][0]["satisfiability"]["structural_at_k"] is False


def test_local_index_none_falls_back_to_ordinal():
    assert chunk_identity({"document": "A", "local_index": None, "ordinal": 0}) == ("loc", "A", 0)
    assert chunk_identity({"document": "A", "ordinal": 0}) == ("loc", "A", 0)
    assert chunk_identity({"document": "A", "local_index": 0, "ordinal": 5}) == ("loc", "A", 0)
    assert chunk_identity({"document": "A", "local_index": "0"}) == ("loc", "A", 0)
    assert chunk_identity({"document": "A", "local_index": "", "ordinal": 0}) == ("loc", "A", 0)
    assert chunk_identity({"document": "A", "local_index": False, "ordinal": 2}) == ("loc", "A", 2)
    assert chunk_identity({"document": "A", "local_index": 1.5, "ordinal": 2}) == ("loc", "A", 2)
    assert chunk_identity({"document": "A", "local_index": -1, "ordinal": 2}) == ("loc", "A", 2)
    assert chunk_identity({"document": "A", "local_index": None, "ordinal": None, "id": "uuid"}) == (
        "id",
        "uuid",
        "A",
    )
    assert chunk_identity({"document": "A", "local_index": "nope"}) is None
    assert chunk_identity({"local_index": 0, "ordinal": 0}) is None
    assert chunk_identity({"document": "A", "local_index": "²", "ordinal": 0}) == ("loc", "A", 0)
    assert chunk_identity({"document": "A", "local_index": "①", "ordinal": 3}) == ("loc", "A", 3)
    assert chunk_identity({"document": "A", "local_index": "²2", "ordinal": 4}) == ("loc", "A", 4)
    assert chunk_identity({"document": "A", "local_index": "２"}) == ("loc", "A", 2)
    assert chunk_identity({"document": "A", "local_index": "²2"}) is None


def test_quote_or_and_document_constraint(tmp_path):
    questions = [
        _question(
            "Q",
            [
                {"document": "A", "quote": ["alpha quote", "alias quote"]},
                {"document": "A", "quote": "beta quote"},
            ],
        )
    ]
    evidence = [
        {"id": "c1", "document": "B", "text": "alias quote beta quote"},
        {"id": "c2", "document": "A", "text": "prefix alias quote suffix"},
    ]
    report = _run(tmp_path, questions, [_case("Q", evidence, stored_hit=False)])
    coverage = report["records"][0]["coverage"]
    assert coverage["items"][0]["matched"] is True
    assert coverage["items"][1]["matched"] is False
    assert coverage["matched"] == 1
    assert coverage["total"] == 2
    assert coverage["and_hit"] is False
    assert report["records"][0]["legacy"]["retrieval_support_hit"] is False


def test_legacy_support_hit_uses_the_live_function(tmp_path):
    question = _question("Q", [{"document": "A", "quote": "alpha quote"}])
    evidence = [{"id": "c1", "document": "A", "material_version_id": "mv", "text": "alpha quote"}]
    ranked = ["A"]
    expected = {"A"}
    ir = evaluate_ranking(ranked, expected, (1, 3))
    report = _run(
        tmp_path,
        [question],
        [_case("Q", evidence, stored_hit=True, retrieval_ir=ir)],
    )
    row = report["records"][0]
    live = support_hit(question, evidence, {"mv": "A"})
    assert row["legacy"]["retrieval_support_hit"] is live is True
    assert row["legacy_drift"] == []
    assert row["coverage"]["and_hit"] is True

    drifted = _run(
        tmp_path / "drift",
        [question],
        [_case("Q", evidence, stored_hit=False, retrieval_ir={**ir, "hit@1": 0.0})],
    )
    fields = {item["field"] for item in drifted["records"][0]["legacy_drift"]}
    assert "retrieval_support_hit" in fields
    assert "retrieval_ir.hit@1" in fields


def test_empty_gold_and_unanswerable_are_not_fake_hits(tmp_path):
    questions = [
        _question("empty", []),
        _question("none", [], answerable=False),
    ]
    cases = [
        _case("empty", [], stored_hit=True),
        _case("none", [], answer="", stored_hit=None),
    ]
    report = _run(tmp_path, questions, cases)
    empty, none = report["records"]
    assert empty["coverage"]["status"] == "empty_gold"
    assert empty["coverage"]["and_hit"] is None
    assert empty["legacy"]["retrieval_support_hit"] is True
    assert none["coverage"]["status"] == "not_applicable"
    assert none["legacy"]["retrieval_support_hit"] is None
    assert none["satisfiability"]["structural_at_k"] == "not_applicable"
    summary = report["summary"]["baseline"]
    assert summary["legacy_support_denominator"] == 1
    assert summary["coverage_denominator"] == 0
    assert summary["empty_support"] == 1
    assert summary["unanswerable"] == 1
    assert summary["span_micro_total"] == 0
    assert summary["span_macro_denominator"] == 0
    assert summary["span_micro"] is None
    assert summary["span_macro"] is None


def test_observed_scope_does_not_claim_structural_failure(tmp_path):
    questions = [_question("Q", [{"document": "A", "quote": "missing quote"}])]
    report = _run(tmp_path, questions, [_case("Q", [{"id": "c1", "document": "A", "text": "other"}])])
    row = report["records"][0]
    assert row["coverage"]["and_hit"] is False
    assert row["satisfiability"]["scope"] == "observed"
    assert row["satisfiability"]["structural_at_k"] == "unknown"
    assert row["satisfiability"]["observed_covered"] is False
    assert row["legacy"]["retrieval_ir"]["hit@1"] == 1.0
    seen = _run(
        tmp_path / "seen",
        questions,
        [_case("Q", [{"document": "A", "text": "missing quote"}])],
    )
    assert seen["records"][0]["coverage"]["and_hit"] is True
    assert seen["records"][0]["satisfiability"]["observed_covered"] is True
    assert seen["records"][0]["satisfiability"]["scope"] == "observed"
    assert seen["records"][0]["satisfiability"]["minimum"] is None


def test_bounded_cover_keeps_lower_and_upper_apart(tmp_path):
    sets = [{1, 2}, {1, 3}, {1, 4}, {2, 3, 4}]
    bounded = cover_bounds(sets, allow_exact=False)
    assert packing_lower(sets) == 1
    assert greedy_upper(sets) == 2
    assert bounded["algorithm"] == "bounded"
    assert bounded["lower"] == 1
    assert bounded["upper"] == 2
    assert bounded["minimum"] is None
    assert bounded["lower"] != greedy_upper(sets)
    exact = cover_bounds(sets)
    assert exact["algorithm"] == "exact"
    assert exact["minimum"] == exact["lower"] == exact["upper"] == 2

    supports = []
    chunks = []
    for index in range(17):
        token = f"QTOK{index:02d}"
        supports.append({"document": "D", "quote": token})
    chunks.append({"document": "D", "local_index": 0, "text": " ".join(f"QTOK{i:02d}" for i in range(9))})
    chunks.append({"document": "D", "local_index": 1, "text": " ".join(["QTOK00", *(f"QTOK{i:02d}" for i in range(9, 17))])})
    for index in range(1, 17):
        chunks.append({"document": "D", "local_index": index + 1, "text": f"QTOK{index:02d}"})
    manifest = {"complete": True, "chunks": chunks}
    report = _run(tmp_path, [_question("Q", supports)], [_case("Q", [])], manifest=manifest, top_k=1)
    row = report["records"][0]["satisfiability"]
    assert row["algorithm"] == "bounded"
    assert row["lower"] == 1
    assert row["upper"] == 2
    assert row["minimum"] is None
    assert row["structural_at_k"] == "unknown"
    assert row["lower"] != row["upper"]


def test_upper_bound_can_prove_possible_and_lower_bound_impossible(tmp_path):
    supports = [{"document": "D", "quote": f"QTOK{index:02d}"} for index in range(17)]
    chunks = [
        {"document": "D", "local_index": 0, "text": " ".join(f"QTOK{i:02d}" for i in range(9))},
        {
            "document": "D",
            "local_index": 1,
            "text": " ".join(["QTOK00", *(f"QTOK{i:02d}" for i in range(9, 17))]),
        },
    ]
    for index in range(1, 17):
        chunks.append({"document": "D", "local_index": index + 1, "text": f"QTOK{index:02d}"})
    manifest = {"complete": True, "chunks": chunks}
    possible = _run(tmp_path, [_question("Q", supports)], [_case("Q", [])], manifest=manifest, top_k=2)
    impossible = _run(
        tmp_path / "low",
        [_question("Q", supports)],
        [_case("Q", [])],
        manifest=manifest,
        top_k=0,
    )
    assert possible["records"][0]["satisfiability"]["structural_at_k"] is True
    assert impossible["records"][0]["satisfiability"]["structural_at_k"] is False
    assert possible["records"][0]["satisfiability"]["minimum"] is None


def test_top_k_labeled_complete_stays_observed_and_real_manifest_still_scores(tmp_path):
    questions = [_question("Q", [{"document": "A", "quote": "alpha quote"}])]
    top = [{"id": "c1", "document": "A", "text": "other text"}]
    copied = {"complete": True, "chunks": list(top)}
    observed = _run(tmp_path, questions, [_case("Q", top)], manifest=copied, top_k=1)
    row = observed["records"][0]["satisfiability"]
    assert row["scope"] == "observed"
    assert row["structural_at_k"] == "unknown"
    assert row["algorithm"] == "unknown"
    assert row["corpus_verified"] is False
    assert "observed retrieval" in row["validation_error"]

    sourced = {"complete": True, "source": "top_k", "chunks": [{"document": "A", "local_index": 1, "text": "other text"}]}
    labeled = _run(tmp_path / "source", questions, [_case("Q", top)], manifest=sourced, top_k=1)
    assert labeled["records"][0]["satisfiability"]["structural_at_k"] == "unknown"

    separate = {"complete": True, "chunks": [{"document": "A", "local_index": 9, "text": "other text"}]}
    corpus = _run(tmp_path / "corpus", questions, [_case("Q", top)], manifest=separate, top_k=1)
    claimed = corpus["records"][0]["satisfiability"]
    assert claimed["algorithm"] == "impossible"
    assert claimed["structural_at_k"] is False
    assert claimed["corpus_verified"] is False
    assert claimed["universe_trust"] == "caller_asserted"


def test_partial_manifest_stays_observed(tmp_path):
    questions = [_question("Q", [{"document": "A", "quote": "alpha quote"}])]
    manifest = {"complete": True, "candidate_complete": False, "chunks": []}
    report = _run(tmp_path, questions, [_case("Q", [])], manifest=manifest)
    assert report["records"][0]["satisfiability"]["scope"] == "observed"
    assert report["records"][0]["satisfiability"]["structural_at_k"] == "unknown"


def test_annotations_keep_source_and_do_not_open_the_gate(tmp_path):
    question = _question("Q", [{"document": "A", "quote": "alpha quote"}])
    evidence = [{"id": "c1", "document": "A", "text": "alpha quote"}]
    human = {
        "question_id": "Q",
        "strategy": "baseline",
        "label": "answered",
        "source": "human",
        "status": "confirmed",
        "provenance": "reviewer note",
        "support_roles": [{"support_index": 0, "role": "necessary"}],
    }
    ai = {**human, "label": "refused", "source": "ai", "provenance": "model note"}
    report = _run(
        tmp_path,
        [question],
        [_case("Q", evidence, answer="答案正确。资料不足。")],
        annotations=[human, ai],
    )
    quality = report["records"][0]["quality"]
    assert quality["class"] == "answered"
    assert quality["method"] == "annotation_human"
    assert quality["ground_truth"] is False
    assert quality["ai_annotation_label"] == "refused"
    assert quality["ai_is_human"] is False
    assert quality["refusal_marker"] is True
    assert report["records"][0]["coverage"]["items"][0]["role"] == "necessary"
    assert report["records"][0]["coverage"]["items"][0]["role_source"] == "human"
    assert report["gate"]["eligible_for_default"] is False
    assert report["gate"]["manual_review_complete"] is False

    ai_only = _run(
        tmp_path / "ai",
        [question],
        [_case("Q", evidence, answer="没有标记")],
        annotations=[{**ai}],
    )
    assert ai_only["records"][0]["quality"]["class"] == "unknown"
    assert ai_only["records"][0]["quality"]["ai_annotation_label"] == "refused"
    assert ai_only["records"][0]["quality"]["refusal_marker"] is False
    assert ai_only["records"][0]["coverage"]["items"][0]["role"] == "unknown"

    conflict = _run(
        tmp_path / "conflict",
        [question],
        [_case("Q", evidence, answer="拒答但没有标记")],
        annotations=[
            human,
            {**human, "label": "contradictory", "provenance": "second reviewer"},
        ],
    )
    assert conflict["records"][0]["quality"]["class"] == "unknown"
    assert conflict["records"][0]["quality"]["method"] == "annotation_conflict"

    missing = _run(
        tmp_path / "missing",
        [question],
        [_case("Q", evidence)],
        annotations=[{**human, "provenance": ""}],
    )
    assert missing["records"][0]["quality"]["class"] == "unknown"
    assert missing["records"][0]["quality"]["ignored_annotations"] == 1
    assert missing["records"][0]["citation"]["faithful"] == "unknown"

    leaked = _run(
        tmp_path / "strategy",
        [question],
        [
            _case("Q", evidence, strategy="baseline", answer="没有标记"),
            _case("Q", evidence, strategy="rewrite", answer="没有标记"),
        ],
        annotations=[{**human, "label": "refused"}],
    )
    by_strategy = {row["strategy"]: row["quality"]["class"] for row in leaked["records"]}
    assert by_strategy == {"baseline": "refused", "rewrite": "unknown"}
    unlabeled = {**human}
    unlabeled.pop("strategy")
    omitted = _run(
        tmp_path / "omitted",
        [question],
        [_case("Q", evidence)],
        annotations=[unlabeled],
    )
    assert omitted["records"][0]["quality"]["class"] == "unknown"


def test_role_conflict_returns_unknown(tmp_path):
    question = _question("Q", [{"document": "A", "quote": "alpha quote"}])
    base = {
        "question_id": "Q",
        "strategy": "baseline",
        "label": "partial",
        "source": "human",
        "status": "confirmed",
        "provenance": "note",
    }
    report = _run(
        tmp_path,
        [question],
        [_case("Q", [{"id": "c1", "document": "A", "text": "alpha quote"}])],
        annotations=[
            {**base, "support_roles": [{"support_index": 0, "role": "necessary"}]},
            {
                **base,
                "provenance": "other",
                "support_roles": [{"support_index": 0, "role": "supplementary"}],
            },
        ],
    )
    item = report["records"][0]["coverage"]["items"][0]
    assert item["role"] == "unknown"
    assert item["role_source"] == "conflict"


def test_strategies_and_cli(tmp_path):
    question = _question("Q", [{"document": "A", "quote": "alpha quote"}])
    evidence = [{"id": "c1", "document": "A", "text": "alpha quote"}]
    report_path = tmp_path / "report.jsonl"
    questions_path = tmp_path / "questions.jsonl"
    report_path.write_text(
        "\n".join(
            json.dumps(_case("Q", evidence, strategy=strategy), ensure_ascii=False)
            for strategy in ("baseline", "rewrite")
        )
        + "\n",
        encoding="utf-8",
    )
    questions_path.write_text(json.dumps(question) + "\n", encoding="utf-8")
    output = tmp_path / "out"
    main(
        [
            "--report",
            str(report_path),
            "--questions",
            str(questions_path),
            "--output-dir",
            str(output),
        ]
    )
    saved = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert saved["schema_version"] == "evaluation-v2.2"
    assert set(saved["summary"]) == {"baseline", "rewrite"}
    assert saved["provenance"]["model_calls"] == 0
    assert saved["provenance"]["inputs"][0]["sha256"]
    assert (output / "records.csv").exists()
    assert (output / "summary.md").exists()
    assert "eligible_for_default" in (output / "summary.md").read_text(encoding="utf-8")
    assert saved["integration_baseline"]["live_comparison"] == "NOT_VERIFIED"
    assert saved["integration_baseline"]["cost"]["status"] == "NOT_VERIFIED"
    assert saved["gate"]["eligible_for_default"] is False


def test_span_macro_and_micro_keep_separate_denominators(tmp_path):
    questions = [
        _question(
            "A",
            [
                {"document": "A", "quote": "alpha quote"},
                {"document": "A", "quote": "beta quote"},
            ],
        ),
        _question("B", [{"document": "A", "quote": "alpha quote"}]),
        _question("E", []),
        _question("U", [], answerable=False),
    ]
    partial = [{"id": "c1", "document": "A", "text": "alpha quote"}]
    both = [{"id": "c1", "document": "A", "text": "alpha quote beta quote"}]
    cases = []
    for strategy, evidence in (("baseline", partial), ("rewrite", both)):
        cases.append(_case("A", evidence, strategy=strategy))
        cases.append(_case("B", partial, strategy=strategy))
        cases.append(_case("E", [], strategy=strategy))
        cases.append(_case("U", [], strategy=strategy, answer="资料不足"))
    report = _run(tmp_path, questions, cases)
    baseline = report["summary"]["baseline"]
    rewrite = report["summary"]["rewrite"]
    assert baseline["span_micro_matched"] == 2
    assert baseline["span_micro_total"] == 3
    assert baseline["span_micro"] == 2 / 3
    assert baseline["span_macro"] == (0.5 + 1) / 2
    assert baseline["span_macro_denominator"] == 2
    assert baseline["coverage_denominator"] == 2
    assert baseline["empty_support"] == 1
    assert baseline["unanswerable"] == 1
    assert rewrite["span_micro_matched"] == 3
    assert rewrite["span_micro_total"] == 3
    assert rewrite["span_macro_denominator"] == 2
    assert rewrite["empty_support"] == 1
    assert report["records"][0]["legacy"]["retrieval_support_hit"] is False
    assert report["records"][1]["legacy"]["retrieval_support_hit"] is True


def test_saved_record_shape_and_stored_latency_do_not_invent_cost(tmp_path):
    question = _question("Q", [{"document": "A", "quote": "alpha quote"}])
    questions_path = tmp_path / "questions.jsonl"
    report_path = tmp_path / "report.jsonl"
    questions_path.write_text(json.dumps(question) + "\n", encoding="utf-8")
    record = {
        "question_id": "Q",
        "strategy": "baseline",
        "answer": "alpha quote",
        "evidence": [{"id": "c1", "document": "A", "text": "alpha quote"}],
        "citations": ["c1"],
        "metrics": {"retrieval_support_hit": True, "refusal_marker": False},
        "retrieval_ms": 12,
        "answer_metrics": {"elapsed_ms": 30, "usage": {"total_tokens": 8}},
    }
    report_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    report = evaluate_paths(report_path, questions_path)
    row = report["records"][0]
    assert row["legacy"]["retrieval_support_hit"] is True
    assert row["legacy_drift"] == []
    assert row["quality"]["class"] == "unknown"
    baseline = report["integration_baseline"]
    assert baseline["retrieval_latency_ms"]["status"] == "measured"
    assert baseline["retrieval_latency_ms"]["value"] == 12
    assert baseline["answer_latency_ms"]["value"] == 30
    assert baseline["answer_tokens"]["value"] == 8
    assert baseline["cost"]["status"] == "NOT_VERIFIED"
    assert baseline["live_comparison"] == "NOT_VERIFIED"
    assert report["gate"]["eligible_for_default"] is False
