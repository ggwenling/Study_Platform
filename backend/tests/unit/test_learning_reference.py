"""Reference answers must not leak before the learner submits or is graded."""

from types import SimpleNamespace

from study_platform.services.learning import _reference_revealed


def test_reference_hidden_until_image_or_verdict():
    pending = SimpleNamespace(verdict=None, answer={"type": "pending"})
    assert _reference_revealed(pending) is False
    answered = SimpleNamespace(verdict=None, answer={"type": "image", "ocr_text": "x"})
    assert _reference_revealed(answered) is True
    graded = SimpleNamespace(verdict="pending", answer={"type": "pending"})
    assert _reference_revealed(graded) is True


def test_attempt_payload_omits_reference_for_unanswered_items():
    from uuid import uuid4

    from study_platform.services import learning

    owner = uuid4()
    attempt_id = uuid4()
    quiz_id = uuid4()
    item_id = uuid4()
    response_id = uuid4()

    resp = SimpleNamespace(
        id=response_id,
        verdict=None,
        verdict_source=None,
        confidence=None,
        feedback=None,
        answer={"type": "pending"},
        reference_answer_effective=None,
    )
    item = SimpleNamespace(
        id=item_id,
        ordinal=0,
        stem="题干",
        item_type="short_answer",
        answer_key={"text": "secret ref"},
        rubric={},
    )
    attempt = SimpleNamespace(id=attempt_id, quiz_id=quiz_id, status="in_progress")
    quiz = SimpleNamespace(course_id=uuid4())

    class FakeResult:
        def all(self):
            return [(resp, item)]

    class FakeSession:
        def execute(self, stmt):
            if "quiz_attempts" in str(stmt).lower():
                return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: attempt))
            return FakeResult()

        def get(self, _model, _id):
            return quiz

    payload = learning.attempt_payload(FakeSession(), attempt_id, owner)
    assert payload["items"][0]["stem"] == "题干"
    assert "reference_answer" not in payload["items"][0]