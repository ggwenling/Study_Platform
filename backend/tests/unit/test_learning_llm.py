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
    monkeypatch.setattr("study_platform.services.learning_grade.llm_available", lambda _: True)
    monkeypatch.setattr(
        "study_platform.services.learning_grade.grade_with_llm",
        lambda stem, ref, ans, **_: GradeResult("correct", 0.5, "部分一致"),
    )
    verdict, conf, feedback, profile = grade_text_answer("完全不同", "参考答案", 0.9, stem="题干")
    assert verdict == "pending" and conf == 0.5
    assert profile == "learning_llm_v1"
    assert "部分一致" in feedback


def test_parse_mcq_question_and_deterministic_mcq_grading():
    from types import SimpleNamespace

    from study_platform.services.learning import _grade_mcq_choice

    payload = json.dumps(
        {
            "questions": [
                {
                    "item_type": "mcq",
                    "stem": "关于特征值，下列哪项正确？",
                    "options": ["A. 特征向量可以为零向量", "B. 特征向量必须非零", "C. 只能是实数", "D. 与行列式无关"],
                    "reference_answer": "B",
                    "explanation": "按定义特征向量非零。",
                    "source_indices": [1],
                },
                {
                    "item_type": "mcq",
                    "stem": "缺少选项的坏题",
                    "options": ["A. 唯一选项"],
                    "reference_answer": "A",
                    "source_indices": [1],
                },
            ]
        }
    )
    questions, insufficient = parse_quiz_response(payload, 2)
    assert insufficient is None
    assert len(questions) == 1
    assert questions[0].item_type == "mcq"
    assert len(questions[0].options) == 4
    assert questions[0].explanation == "按定义特征向量非零。"

    item = SimpleNamespace(
        answer_key={"choice": "B", "text": "B"},
        options=list(questions[0].options),
        explanation=questions[0].explanation,
    )
    verdict_ok, conf_ok, fb_ok, _, _ = _grade_mcq_choice(item, "B. 特征向量必须非零")
    assert verdict_ok == "correct" and conf_ok == 1.0 and "非零" in fb_ok
    verdict_bad, _, fb_bad, _, _ = _grade_mcq_choice(item, "A. 特征向量可以为零向量")
    assert verdict_bad == "incorrect" and "B" in fb_bad



def test_parse_mcq_normalizes_unprefixed_options_and_text_or_wrapped_reference():
    from types import SimpleNamespace

    from study_platform.services.learning import _grade_mcq_choice

    payload = json.dumps(
        {
            "questions": [
                {
                    "item_type": "mcq",
                    "stem": "无前缀选项 + 字母答案",
                    "options": ["零向量", "非零向量", "奇异矩阵", "标量"],
                    "reference_answer": "B",
                    "source_indices": [1],
                },
                {
                    "item_type": "mcq",
                    "stem": "带前缀选项 + 选项正文答案",
                    "options": ["A. 零向量", "B. 非零向量", "C. 奇异矩阵", "D. 标量"],
                    "reference_answer": "非零向量",
                    "source_indices": [1],
                },
                {
                    "item_type": "mcq",
                    "stem": "带前缀选项 + 包装字母答案",
                    "options": ["(A) 零向量", "(B) 非零向量", "(C) 奇异矩阵", "(D) 标量"],
                    "reference_answer": "答案：(B)",
                    "source_indices": [1],
                },
                {
                    "item_type": "mcq",
                    "stem": "无法匹配任何选项的坏单选题",
                    "options": ["A. 甲", "B. 乙"],
                    "reference_answer": "不存在的丙",
                    "source_indices": [1],
                },
            ]
        }
    )
    questions, insufficient = parse_quiz_response(payload, 2)
    assert insufficient is None
    assert len(questions) == 3

    for q in questions:
        assert q.options == ("A. 零向量", "B. 非零向量", "C. 奇异矩阵", "D. 标量")
        item = SimpleNamespace(
            answer_key={"text": q.reference_answer},
            options=list(q.options),
            explanation=None,
        )
        assert _grade_mcq_choice(item, "B. 非零向量")[0] == "correct"
        assert _grade_mcq_choice(item, "非零向量")[0] == "correct"
        assert _grade_mcq_choice(item, "B")[0] == "correct"
        assert _grade_mcq_choice(item, "A. 零向量")[0] == "incorrect"


def test_mcq_full_option_body_takes_precedence_over_letter():
    from study_platform.services.learning_llm import resolve_mcq_choice

    options = ["A. 常规方案", "B. A 方案适用", "C. 答案是 A"]
    assert resolve_mcq_choice("A 方案适用", options) == "B"
    assert resolve_mcq_choice("答案是 A", options) == "C"
    assert resolve_mcq_choice("A", options) == "A"
    assert resolve_mcq_choice("B. A 方案适用", options) == "B"
    assert resolve_mcq_choice("A. 常规方案", ["A. 常规方案", "B. A. 常规方案"]) == "A"


def test_mcq_standalone_letter_takes_precedence_over_matching_option_body():
    from types import SimpleNamespace

    from study_platform.services.learning import _grade_mcq_choice
    from study_platform.services.learning_llm import resolve_mcq_choice

    options = ["A. B", "B. A", "C. A star"]
    raw = json.dumps({"questions": [{
        "item_type": "mcq", "stem": "Which option contains A?", "options": options,
        "reference_answer": "B", "source_indices": [1],
    }]})
    questions, _ = parse_quiz_response(raw, 1)
    assert questions[0].reference_answer == "B"
    assert resolve_mcq_choice("（Ｂ）", options) == "B"
    assert resolve_mcq_choice("A star", options) == "C"

    item = SimpleNamespace(answer_key={"choice": "B", "text": "B"}, options=options, explanation=None)
    assert _grade_mcq_choice(item, "B")[0] == "correct"
    assert _grade_mcq_choice(item, "B. A")[0] == "correct"
    assert _grade_mcq_choice(item, "A")[0] == "incorrect"


def test_generated_types_respect_requested_mode_and_retry(monkeypatch):
    monkeypatch.setattr(
        "study_platform.services.learning_llm.get_settings",
        lambda: Settings(_env_file=None, learning_llm_max_retries=1),
    )
    short = {"item_type": "short_answer", "stem": "简答", "reference_answer": "答案", "source_indices": [1]}
    mcq = {"item_type": "mcq", "stem": "单选", "reference_answer": "A", "options": ["一", "二"], "source_indices": [1]}
    invalid = {**short, "item_type": "unknown"}
    mislabeled = {**mcq, "item_type": "short_answer"}
    for mode, wrong, correct in [("mcq", short, mcq), ("short_answer", mcq, short)]:
        calls = []
        def invoker(_system, _user, *, retry_hint=""):
            calls.append(retry_hint)
            return json.dumps({"questions": [wrong, invalid, mislabeled] if len(calls) == 1 else [correct]})
        questions, _ = generate_questions_with_llm(_frag(), 1, mode=mode, invoker=invoker)
        assert [q.item_type for q in questions] == [mode]
        assert len(calls) == 2
    questions, _ = generate_questions_with_llm(
        _frag(), 3, mode="mixed", invoker=lambda *_args, **_kwargs: json.dumps({"questions": [mcq, short, invalid]})
    )
    assert [q.item_type for q in questions] == ["mcq", "short_answer"]
