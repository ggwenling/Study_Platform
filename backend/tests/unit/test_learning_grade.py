from study_platform.services.learning_grade import grade_text_answer, normalize_answer


def test_normalize_answer_strips_space():
    assert normalize_answer("  Hello　世界 ") == "hello世界"


def test_grade_pending_on_empty_ocr():
    verdict, conf, _, profile = grade_text_answer("", "答案", 0.9)
    assert verdict == "pending" and conf == 0.0
    assert profile == "learning_stub_v1"


def test_grade_correct_on_match():
    verdict, conf, feedback, profile = grade_text_answer("答案", "答案", 0.8)
    assert verdict == "correct" and conf >= 0.85
    assert "一致" in feedback
    assert profile == "learning_stub_v1"


def test_grade_pending_on_mismatch_below_threshold(monkeypatch):
    from study_platform.core.config import get_settings

    settings = get_settings().model_copy(update={"learning_stub_grader": True, "learning_confidence_threshold": 0.85})
    monkeypatch.setattr("study_platform.services.learning_grade.get_settings", lambda: settings)
    verdict, conf, _, profile = grade_text_answer("完全不对", "参考答案", 0.7)
    assert verdict == "pending" and conf < 0.85
    assert profile == "learning_stub_v1"