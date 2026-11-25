"""Unit checks for quiz chunk sampling fallback on cross-lingual or sparse retrieval."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from study_platform.core.errors import ApiError
from study_platform.services import learning


def _chunk(material_version_id, ordinal: int, text: str, title: str):
    return (
        SimpleNamespace(
            id=uuid4(),
            index_version_id=uuid4(),
            material_version_id=material_version_id,
            ordinal=ordinal,
            text=text,
            locator={"start_line": ordinal + 1, "end_line": ordinal + 1},
        ),
        title,
    )


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _stmt):
        return SimpleNamespace(all=lambda: self._rows)


def test_collect_quiz_hits_falls_back_round_robin_when_retrieval_is_empty(monkeypatch):
    owner = uuid4()
    mv_a, mv_b = uuid4(), uuid4()
    rows = [
        _chunk(mv_a, 0, "Fresh responses can be reused without validation.", "rfc9111.txt"),
        _chunk(mv_b, 0, "The immutable directive applies during freshness lifetime.", "rfc8246.txt"),
        _chunk(mv_a, 1, "Stale responses normally require revalidation.", "rfc9111.txt"),
        _chunk(mv_b, 1, "Clients should not send conditional requests while fresh.", "rfc8246.txt"),
    ]
    seen_queries: list[str] = []
    monkeypatch.setattr(
        learning,
        "retrieve",
        lambda _scope, query, _owner: seen_queries.append(query) or [],
    )
    hits = learning._collect_quiz_hits(
        _FakeDb(rows),
        {"index_version_ids": [str(uuid4())]},
        [str(uuid4())],
        owner,
        count=3,
        stub_mode=False,
    )
    assert seen_queries and "key concepts" in seen_queries[0]
    assert len(hits) == 4
    assert [hit["title"] for hit in hits] == [
        "rfc9111.txt",
        "rfc8246.txt",
        "rfc9111.txt",
        "rfc8246.txt",
    ]


def test_collect_quiz_hits_supplements_sparse_retrieval_without_duplicates(monkeypatch):
    owner = uuid4()
    mv_a = uuid4()
    first_row, first_title = _chunk(mv_a, 0, "Clause one.", "doc.txt")
    second_row, second_title = _chunk(mv_a, 1, "Clause two.", "doc.txt")
    retrieved = [learning.chunk_dto(first_row, first_title)]
    monkeypatch.setattr(learning, "retrieve", lambda *_args: retrieved)
    hits = learning._collect_quiz_hits(
        _FakeDb([(second_row, second_title)]),
        {"index_version_ids": [str(uuid4())]},
        [str(uuid4())],
        owner,
        count=3,
        stub_mode=False,
    )
    assert [hit["id"] for hit in hits] == [first_row.id, second_row.id]


def test_generate_learning_quiz_still_raises_no_chunks_when_index_has_no_text(monkeypatch):
    owner = uuid4()
    kb_id = uuid4()
    fake_kb = SimpleNamespace(id=kb_id, name="Empty KB")

    class _EmptyDb:
        def execute(self, _stmt):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(first=lambda: fake_kb),
                all=lambda: [],
            )

    monkeypatch.setattr(learning, "visible_indices", lambda *_args: [str(uuid4())])
    monkeypatch.setattr(learning, "retrieve", lambda *_args: [])
    monkeypatch.setattr(
        learning,
        "get_settings",
        lambda: SimpleNamespace(learning_stub_grader=False),
    )
    with pytest.raises(ApiError) as error:
        learning.generate_learning_quiz.__wrapped__(
            _EmptyDb(),
            owner_user_id=owner,
            knowledge_base_id=kb_id,
            count=3,
        )
    assert error.value.status_code == 409 and error.value.code == "no_chunks"
