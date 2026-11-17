from study_platform.services.learning_grade import grade_text_answer, normalize_answer


def test_normalize_answer_strips_space():
    assert normalize_answer("  Hello　世界 ") == "hello世界"


def test_grade_pending_on_empty_ocr():
    verdict, conf, _, profile = grade_text_answer("", "答案", 0.9)
    assert verdict == "pending" and conf == 0.0
    assert profile == "learning_stub_v1"


def test_grade_correct_on_match():
    verdict, conf, feedback, profile = grade_text_answer("答案", "答案", 0.9)
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

def test_invalid_image_is_rejected_before_ocr(monkeypatch):
    import pytest

    from study_platform.core.errors import ApiError
    from study_platform.services.learning_grade import ocr_answer_bytes

    monkeypatch.setattr("study_platform.services.learning_grade.ocr_image", lambda image: pytest.fail("unexpected OCR"))
    with pytest.raises(ApiError) as error:
        ocr_answer_bytes(b"not an image")
    assert error.value.code == "invalid_image"


def test_substring_and_low_confidence_do_not_auto_pass(monkeypatch):
    from study_platform.core.config import get_settings

    settings = get_settings().model_copy(update={"learning_stub_grader": True, "learning_confidence_threshold": 0.85})
    monkeypatch.setattr("study_platform.services.learning_grade.get_settings", lambda: settings)
    for actual, reference, confidence in [("10", "100", 0.99), ("不正确", "正确", 0.99), ("100", "100", 0.2)]:
        assert grade_text_answer(actual, reference, confidence)[0] == "pending"


def test_answer_image_commit_failure_preserves_existing_file(tmp_path, monkeypatch):
    from io import BytesIO
    from types import SimpleNamespace
    from uuid import uuid4

    import pytest
    from starlette.datastructures import Headers, UploadFile

    from study_platform.api.v1 import learning as endpoint
    from study_platform.core.config import get_settings

    old = tmp_path / "old.image"
    old.write_bytes(b"previous")
    new = tmp_path / "new.image"
    new.write_bytes(b"new")
    row = SimpleNamespace(answer={"storage_key": new.name})
    settings = get_settings().model_copy(update={"storage_dir": tmp_path, "max_upload_bytes": 20})
    monkeypatch.setattr(endpoint, "get_settings", lambda: settings)
    monkeypatch.setattr(endpoint.learning, "submit_image_answer", lambda *args, **kwargs: row)
    class FailedSession:
        def commit(self):
            raise RuntimeError("commit failed")
        def rollback(self):
            pass
    upload = UploadFile(file=BytesIO(b"new"), headers=Headers({"content-type": "image/png"}))
    with pytest.raises(RuntimeError, match="commit failed"):
        endpoint.answer_image(uuid4(), upload, SimpleNamespace(id=uuid4()), FailedSession())
    assert old.read_bytes() == b"previous"
    assert not new.exists()


def test_answer_image_commit_removes_previous_image(tmp_path, monkeypatch):
    from io import BytesIO
    from types import SimpleNamespace
    from uuid import uuid4

    from starlette.datastructures import Headers, UploadFile

    from study_platform.api.v1 import learning as endpoint
    from study_platform.core.config import get_settings

    previous = tmp_path / "answer.png"
    previous.write_bytes(b"old")
    current = tmp_path / "new.image"
    current.write_bytes(b"new")
    row = SimpleNamespace(id=uuid4(), answer={"storage_key": current.name},
                          _previous_image_path=previous, verdict="pending", verdict_source="auto",
                          confidence=0.5, feedback="", reference_answer_effective="")
    settings = get_settings().model_copy(update={"storage_dir": tmp_path, "max_upload_bytes": 20})
    monkeypatch.setattr(endpoint, "get_settings", lambda: settings)
    monkeypatch.setattr(endpoint.learning, "submit_image_answer", lambda *args, **kwargs: row)
    upload = UploadFile(file=BytesIO(b"new"), headers=Headers({"content-type": "image/png"}))
    endpoint.answer_image(uuid4(), upload, SimpleNamespace(id=uuid4()), SimpleNamespace(commit=lambda: None))
    assert not previous.exists()
    assert current.read_bytes() == b"new"


def test_answer_image_rejects_oversized_before_processing(monkeypatch):
    from io import BytesIO
    from types import SimpleNamespace
    from uuid import uuid4

    import pytest
    from starlette.datastructures import Headers, UploadFile

    from study_platform.api.v1 import learning as endpoint
    from study_platform.core.config import get_settings
    from study_platform.core.errors import ApiError

    settings = get_settings().model_copy(update={"max_upload_bytes": 4})
    monkeypatch.setattr(endpoint, "get_settings", lambda: settings)
    monkeypatch.setattr(endpoint.learning, "submit_image_answer", lambda *args, **kwargs: pytest.fail("unexpected processing"))
    upload = UploadFile(file=BytesIO(b"123456789"), headers=Headers({"content-type": "image/png"}))
    with pytest.raises(ApiError) as error:
        endpoint.answer_image(uuid4(), upload, SimpleNamespace(id=uuid4()), None)
    assert error.value.status_code == 413
    assert upload.file.tell() == 5
