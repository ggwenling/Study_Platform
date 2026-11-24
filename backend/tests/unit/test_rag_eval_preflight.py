import json
from pathlib import Path

import pytest

from study_platform.cli import evaluate_rag
from study_platform.cli.rag_eval_preflight import digest, load_profile
from study_platform.services.rag_evaluation import read_dataset, support_hit


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def example(tmp_path):
    source = tmp_path / "document.txt"
    source.write_text("The public rate is\n  greater than five.\n\nStatus is eligible.\n", encoding="utf-8")
    case = {
        "id": "one", "fact_group": "one", "split": "heldout", "knowledge_base": "kb",
        "question": "Do these account terms qualify?", "answerable": True,
        "expected_facts": [["eligible"]],
        "support": [{"alternatives": [{"document": "doc", "parts": [
            {"locator": {"start_line": 1, "end_line": 2}, "quote": "rate is greater than five"},
            {"locator": {"start_line": 4, "end_line": 4}, "quote": "Status is eligible"},
        ]}]}],
    }
    dataset = tmp_path / "questions.jsonl"
    materials = tmp_path / "materials.json"
    write_json(dataset, case)
    write_json(materials, [{
        "id": "doc", "format": "txt", "title": "Example", "path": source.name,
        "knowledge_base": "kb", "sha256": digest(source),
    }])
    manifest = tmp_path / "evaluation_manifest.json"
    write_json(manifest, {
        "schema_version": 2, "corpus_root": ".", "dataset_sha256": digest(dataset),
        "materials_sha256": digest(materials),
        "profiles": {"native": {"max_pdf_pages": 150}},
    })
    return dataset, manifest, source


def test_preflight_uses_manifest_root_without_db_or_provider(tmp_path, monkeypatch, capsys):
    dataset, manifest, _ = example(tmp_path)
    monkeypatch.chdir(tmp_path.parent)
    monkeypatch.setattr("sys.argv", ["study-eval-rag", "--dataset", str(dataset),
                                   "--manifest", str(manifest), "--pipeline-profile", "semantic-v4", "--preflight"])
    monkeypatch.setattr(evaluate_rag, "isolated_database", lambda *_: pytest.fail("database visited"))
    monkeypatch.setattr(evaluate_rag, "embed_texts", lambda *_: pytest.fail("model visited"))
    evaluate_rag.main()
    result = json.loads(capsys.readouterr().out)
    assert result["preflight_passed"] and result["status"] == "BLOCKED"
    assert result["evidence"] == [{"id": "one", "missing_requirements": [], "witness_chunks": 2}]


def test_manifest_review_requires_two_bound_receipts(tmp_path):
    dataset, manifest, _ = example(tmp_path)
    config = json.loads(manifest.read_text())
    answer = tmp_path / "answers.json"
    write_json(answer, {"one": "The account qualifies"})
    config.update(answer_key=answer.name, answer_key_sha256=digest(answer), reviews=[])
    for name in ("reviewer-a", "reviewer-b"):
        path = tmp_path / f"{name}.json"
        write_json(path, {
            "reviewer": name, "review_kind": "ai", "dataset_sha256": digest(dataset),
            "answer_key_sha256": digest(answer), "verdicts": {"one": {"status": "PASS", "reason": "Checked"}},
        })
        config["reviews"].append({"path": path.name, "sha256": digest(path)})
    write_json(manifest, config)
    cases, materials = read_dataset(dataset)
    assert load_profile(manifest, dataset, cases, materials, "native")[-1]
    receipt = tmp_path / config["reviews"][0]["path"]
    data = json.loads(receipt.read_text())
    data["reviewer"] = "reviewer-b"
    write_json(receipt, data)
    config["reviews"][0]["sha256"] = digest(receipt)
    write_json(manifest, config)
    assert not load_profile(manifest, dataset, cases, materials, "native")[-1]


def test_bad_source_fails_live_before_database_or_provider(tmp_path, monkeypatch, capsys):
    dataset, _, source = example(tmp_path)
    materials_path = tmp_path / "materials.json"
    materials = json.loads(materials_path.read_text(encoding="utf-8"))
    bad_source = tmp_path / "changed.txt"
    bad_source.write_text("changed since the original SHA-256 was recorded", encoding="utf-8")
    materials.append({**materials[0], "id": "changed", "path": bad_source.name, "sha256": digest(source)})
    write_json(materials_path, materials)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["study-eval-rag", "--dataset", str(dataset), "--live"])
    monkeypatch.setattr(evaluate_rag, "load_dotenv", lambda *_: None)
    monkeypatch.setattr(evaluate_rag, "isolated_database", lambda *_: pytest.fail("database visited"))
    monkeypatch.setattr(evaluate_rag, "embed_texts", lambda *_: pytest.fail("model visited"))
    with pytest.raises(SystemExit, match="preflight failed"):
        evaluate_rag.main()
    summary = json.loads(capsys.readouterr().out)
    assert [item["id"] for item in summary["materials"]] == ["doc"]
    assert summary["failures"][0]["detail"] == "Source SHA-256 mismatch"


def test_support_checks_required_spans_and_location_before_accepting_alternative():
    case = {"support": [
        {"alternatives": [
            {"document": "DOC-A", "parts": [
                {"locator": {"page": 2}, "quote": "size > 60"},
                {"locator": {"page": 3}, "quote": "only if fresh"},
            ]},
            {"document": "DOC-B", "parts": [{"locator": {"page": 3}, "quote": "alternative full rule"}]},
        ]},
    ]}
    docs = {"a": "DOC-A", "b": "DOC-B"}
    positive = {"material_version_id": "a", "locator": {"page": 2}, "text": "size > 60"}
    second = {"material_version_id": "a", "locator": {"page": 3}, "text": "only if fresh"}
    assert support_hit(case, [positive, second], docs)
    assert not support_hit(case, [positive, {**second, "locator": {"page": 4}}], docs)
    assert not support_hit(case, [{**positive, "text": "size < 60"}, second], docs)
    alternate = {"material_version_id": "b", "locator": {"page": 3}, "text": "alternative full rule"}
    assert support_hit(case, [alternate], docs)


def test_symbol_only_alias_cannot_become_unconditional_hit(tmp_path):
    dataset, _, _ = example(tmp_path)
    case = json.loads(dataset.read_text())
    case["expected_facts"] = [[">"]]
    write_json(dataset, case)
    with pytest.raises(ValueError, match="nonempty normalized alias"):
        read_dataset(Path(dataset))
