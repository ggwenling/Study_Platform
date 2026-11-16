"""Deterministic tests for learning LLM JSON parsing, validation, and fallbacks."""

import json
from uuid import uuid4

from study_platform.core.config import Settings, get_settings
from study_platform.services.learning_llm import (
    FragmentRef,
    generate_questions_with_llm,
    grade_with_llm,
    map_fragment_hits,
    parse_grade_response,
    parse_quiz_response,
    strip_json_fence,
)


def _frag(n: int = 2) -> list[FragmentRef]:
    refs = []
    for i in range(1, n + 1):
        refs.append(
            FragmentRef(
                index=i,
                chunk_id=uuid4(),
                index_version_id=uuid4(),
                material_version_id=uuid4(),
                locator={},
                text=f"资料片段{i}：重要概念 Alpha{i}",
                title="Doc",
            )
        )
    return refs


def test_strip_json_fence():
    raw = '```json\n{"questions":[]}\n```'
    assert strip_json_fence(raw) == '{"questions":[]}'


def test_parse_quiz_rejects_invalid_source_index():
    payload = json.dumps(
        {
            "questions": [
                {"stem": "题", "reference_answer": "答", "source_indices": [99]},
            ]
        }
    )
    questions, insufficient = parse_quiz_response(payload, 2)
    assert not questions and insufficient is None


def test_parse_quiz_accepts_valid():
    payload = json.dumps(
        {
            "questions": [
                {"stem": "什么是 Alpha1？", "reference_answer": "概念", "source_indices": [1, 2]},
            ]
        }
    )
    questions, insufficient = parse_quiz_response(payload, 2)
    assert len(questions) == 1
    assert questions[0].source_indices == [1, 2]
    assert insufficient is None


def test_parse_quiz_insufficient_material():
    payload = json.dumps({"questions": [], "insufficient": True, "message": "片段过短"})
    questions, msg = parse_quiz_response(payload, 3)
    assert questions == [] and msg == "片段过短"


def test_map_fragment_hits_skips_empty():
    hits = [{"id": uuid4(), "index_version_id": uuid4(), "material_version_id": uuid4(), "text": "  "}]
    assert map_fragment_hits(hits) == []


def test_generate_questions_retry_then_success(monkeypatch):
    settings = get_settings().model_copy(update={"learning_llm_max_retries": 2})
    monkeypatch.setattr("study_platform.services.learning_llm.get_settings", lambda: settings)
    calls: list[str] = []

    def fake(_system, _user, *, retry_hint: str = ""):
        calls.append(retry_hint)
        if len(calls) == 1:
            return "not-json"
        return json.dumps(
            {
                "questions": [
                    {"stem": "题1", "reference_answer": "答1", "source_indices": [1]},
                ]
            }
        )

    questions, insufficient = generate_questions_with_llm(_frag(), 1, invoker=fake)
    assert len(questions) == 1
    assert insufficient is None
    assert len(calls) == 2


def test_generate_questions_insufficient_from_model(monkeypatch):
    monkeypatch.setattr(
        "study_platform.services.learning_llm.get_settings",
        lambda: Settings(_env_file=None, learning_llm_max_retries=0),
    )

    def fake(_s, _u, *, retry_hint: str = ""):
        return json.dumps({"questions": [], "insufficient": True, "message": "无有效知识点"})

    questions, msg = generate_questions_with_llm(_frag(), 5, invoker=fake)
    assert questions == [] and msg == "无有效知识点"


def test_grade_pending_on_low_confidence_after_parse():
    payload = json.dumps({"verdict": "incorrect", "confidence": 0.4, "reason": "要点缺失"})
    parsed = parse_grade_response(payload)
    assert parsed is not None and parsed.verdict == "incorrect"


def test_grade_with_llm_fallback_to_none(monkeypatch):
    monkeypatch.setattr(
        "study_platform.services.learning_llm.get_settings",
        lambda: Settings(_env_file=None, learning_llm_max_retries=1),
    )

    def bad(_s, _u, *, retry_hint: str = ""):
        return "{broken"

    assert grade_with_llm("题干", "参考答案", "学生答案", invoker=bad) is None


def test_grade_llm_respects_threshold(monkeypatch):
    from study_platform.services.learning_grade import grade_text_answer
    from study_platform.services.learning_llm import GradeResult

    settings = Settings(
        _env_file=None,
        learning_stub_grader=False,
        deepseek_api_key="fake-key",
        learning_confidence_threshold=0.85,
    )
    monkeypatch.setattr("study_platform.services.learning_grade.get_settings", lambda: settings)
    monkeypatch.setattr(
        "study_platform.services.learning_grade.grade_with_llm",
        lambda stem, ref, ans: GradeResult("correct", 0.5, "部分一致"),
    )
    verdict, conf, feedback, profile = grade_text_answer("完全不同", "参考答案", 0.9, stem="题干")
    assert verdict == "pending" and conf == 0.5
    assert profile == "learning_llm_v1"
    assert "部分一致" in feedback