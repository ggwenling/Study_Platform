from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from study_platform.services.learning import _wrong_question_is_due


@pytest.mark.parametrize(
    ("status", "last_wrong_at", "next_review_at", "expected"),
    [
        ("open", datetime(2026, 11, 20, tzinfo=UTC), None, True),
        ("open", datetime(2026, 11, 20, tzinfo=UTC), datetime(2026, 11, 21, tzinfo=UTC), False),
        ("reviewing", datetime(2026, 11, 17, tzinfo=UTC), None, True),
        ("reviewing", datetime(2026, 11, 18, tzinfo=UTC), None, False),
        ("reviewing", datetime(2026, 11, 20, tzinfo=UTC), datetime(2026, 11, 19, tzinfo=UTC), True),
        ("reviewing", datetime(2026, 11, 20, tzinfo=UTC), datetime(2026, 11, 21, tzinfo=UTC), False),
        ("mastered", datetime(2026, 11, 17, tzinfo=UTC), None, False),
    ],
)
def test_wrong_question_due_rules(status, last_wrong_at, next_review_at, expected):
    now = datetime(2026, 11, 20, tzinfo=UTC)
    row = SimpleNamespace(
        status=status,
        last_wrong_at=last_wrong_at,
        next_review_at=next_review_at,
    )

    assert _wrong_question_is_due(row, now) is expected


def test_reviewing_null_schedule_uses_three_day_legacy_fallback():
    now = datetime.now(UTC)
    row = SimpleNamespace(
        status="reviewing",
        last_wrong_at=now - timedelta(days=2),
        next_review_at=None,
    )

    assert not _wrong_question_is_due(row, now)
    row.last_wrong_at = now - timedelta(days=3)
    assert _wrong_question_is_due(row, now)
