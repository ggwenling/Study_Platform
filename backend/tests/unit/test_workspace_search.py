import base64
import json
from uuid import uuid4

import pytest

from study_platform.services.workspace_search import decode_search_cursor, search_excerpt


def test_excerpt_keeps_both_distant_matches_in_the_same_window():
    text = "x" * 90 + "向量" + "x" * 95 + "模型" + "x" * 90
    excerpt = search_excerpt(text, "模型 向量", ["模型", "向量"])
    assert "模型" in excerpt and "向量" in excerpt
    assert len(excerpt.strip("…")) <= 120


def test_search_cursor_rejects_scores_that_overflow_float():
    data = json.dumps(["0x1p2048", 1, str(uuid4())]).encode()
    cursor = base64.urlsafe_b64encode(data).decode().rstrip("=")
    with pytest.raises(ValueError, match="Invalid search cursor"):
        decode_search_cursor(cursor)
