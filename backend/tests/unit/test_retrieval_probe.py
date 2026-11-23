import json
from pathlib import Path

from study_platform.core.config import Settings, get_settings
from study_platform.services.retrieval_probe import (
    load_source_corpus,
    min_chunk_cover,
    synthetic_probe,
)


def test_synthetic_probe_keeps_defaults_and_marks_unmeasured_rankers(monkeypatch):
    monkeypatch.delenv("RETRIEVAL_TOP_K", raising=False)
    monkeypatch.delenv("RETRIEVAL_CANDIDATE_K", raising=False)
    monkeypatch.delenv("RETRIEVAL_LEXICAL_WEIGHT", raising=False)
    get_settings.cache_clear()
    settings = Settings(_env_file=None)
    report = synthetic_probe()
    assert settings.retrieval_top_k == 10
    assert settings.retrieval_candidate_k == 20
    assert settings.retrieval_lexical_weight == 0.0
    assert report["line_chunks"] == 23
    assert report["line_multi"] == 0
    assert report["min_chunks"] == 23
    assert report["exact"] is True
    assert report["and_possible_at_10"] is False
    assert report["and_possible_at_24"] is True
    assert report["lexical_hits_at_10"] == 10
    assert report["lexical_hits_at_24"] == 23
    assert report["packed_min_chunks"] is not None
    assert report["packed_min_chunks"] < 10
    assert report["packed_chunks"] < report["line_chunks"]
    assert report["decisions"] == {
        "chunking_default": "KEEP",
        "top_k_default": "KEEP",
        "lexical_weight_default": "KEEP",
        "hybrid_with_weight_zero": "INVALID_COMPARISON",
    }
    assert report["vector_ranking"] == "NOT_VERIFIED"
    assert report["rerank"] == "NOT_VERIFIED"
    assert report["rewrite_quality"] == "NOT_VERIFIED"
    get_settings.cache_clear()


def test_cover_is_not_exact_when_one_span_is_absent():
    cover = min_chunk_cover(["present token"], ["present token", "other"])
    assert cover["exact"] is True and cover["min_chunks"] == 1
    missing = min_chunk_cover(["absent token"], ["present token"])
    assert missing["exact"] is False and missing["min_chunks"] is None


def test_hash_mismatch_excludes_the_file_and_records_fail(tmp_path):
    source = tmp_path / "rfc-sample.txt"
    source.write_text("alpha\n", encoding="utf-8")
    manifest = {
        "files": [
            {
                "id": "rfc-sample",
                "filename": "rfc-sample.txt",
                "sha256": "0" * 64,
            }
        ]
    }
    documents, files = load_source_corpus(manifest, tmp_path)
    assert documents == {}
    assert files == [{"id": "rfc-sample", "used": False, "status": "FAIL", "sha256": files[0]["sha256"]}]
    assert files[0]["sha256"] != "0" * 64
    text = json.dumps({"documents": documents, "files": files})
    assert "alpha" not in text


def test_matching_hash_loads_text(tmp_path):
    source = tmp_path / "rfc-sample.txt"
    payload = "alpha line\n"
    source.write_text(payload, encoding="utf-8")
    digest = __import__("hashlib").sha256(payload.encode()).hexdigest()
    documents, files = load_source_corpus(
        {"files": [{"id": "rfc-sample", "filename": "rfc-sample.txt", "sha256": digest}]},
        tmp_path,
    )
    assert documents["rfc-sample"] == payload
    assert files[0]["status"] == "PASS" and files[0]["used"] is True


def test_missing_source_is_not_run(tmp_path):
    documents, files = load_source_corpus(
        {
            "files": [
                {
                    "id": "nist-46",
                    "filename": "nist-46.pdf",
                    "sha256_expected_from_eval_report": "ab" * 32,
                    "known_official_byte_mismatch": True,
                }
            ]
        },
        tmp_path,
    )
    assert documents == {}
    assert files[0]["status"] == "NOT_RUN"
    assert files[0]["used"] is False


def test_recorded_rfc_probe_is_counts_only_and_matches_the_known_cover():
    path = Path(__file__).resolve().parents[2] / "evaluation" / "offline_probe_rfc_counts.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["synthetic"] == synthetic_probe()
    assert report["vector_ranking"] == "NOT_VERIFIED"
    assert report["rerank"] == "NOT_VERIFIED"
    assert report["replay_counts"] == {
        "rfc-7234": 1558,
        "rfc-9110": 8658,
        "rfc-9111": 1498,
        "rfc-9112": 1931,
    }
    assert report["replay_multi"] == {name: 0 for name in report["replay_counts"]}
    sources = {row["id"]: row for row in report["sources"]}
    assert sources["rfc-9110"]["status"] == "PASS" and sources["rfc-9110"]["used"] is True
    assert sources["nist-46"]["status"] == "FAIL" and sources["nist-46"]["used"] is False
    assert sources["nist-46"]["sha256"] == (
        "33113108c9867def8465a0679c7acf7de89b08392e165a7ed86349b64ccd762f"
    )
    for name in ("nist-180-4", "nist-186-4", "nist-186-5", "nist-202"):
        assert sources[name]["status"] == "NOT_RUN" and sources[name]["used"] is False
    expected = {
        "REAL-027": 22,
        "REAL-028": 15,
        "REAL-029": 23,
        "REAL-031": 17,
        "REAL-032": 22,
        "REAL-034": 21,
        "REAL-036": 15,
        "REAL-040": 23,
        "REAL-041": 17,
    }
    by_id = {row["id"]: row for row in report["questions"]}
    for question_id, minimum in expected.items():
        row = by_id[question_id]
        assert row["in_chunk"] == row["spans"]
        assert row["min_chunks"] == minimum
    high = [by_id[question_id] for question_id in expected]
    assert sum(row["spans"] for row in high) == 179
    assert sum(row["spans_sharing_query_term"] for row in high) == 51
    assert sum(row["lexical_hits_at_10"] for row in high) == 3
    assert sum(row["lexical_hits_at_24"] for row in high) == 14
    assert sum(row["lexical_hits_at_10"] for row in report["questions"]) == 6
    assert sum(row["lexical_hits_at_24"] for row in report["questions"]) == 17
    assert sum(row["spans_sharing_query_term"] for row in report["questions"]) == 62

    def walk(value):
        if isinstance(value, str):
            assert len(value) <= 80
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(report)
