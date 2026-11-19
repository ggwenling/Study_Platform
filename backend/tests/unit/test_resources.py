"""Resource serialization and Shanghai activity boundary regressions."""

from datetime import UTC, date, datetime
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI

from study_platform.api.v1.resources import activity_days, router
from study_platform.persistence.models import (
    AiWorkflowRun,
    IndexVersion,
    KnowledgeBase,
    Material,
    MaterialChunk,
    MaterialVersion,
)
from study_platform.services.citations import citation_dto, message_dto


class FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def get(self, model, key):
        return self.rows.get((model, key))

    def scalars(self, stmt):
        return SimpleNamespace(all=lambda: [])


def test_citation_snapshot_and_unavailable_sources(monkeypatch, tmp_path):
    from study_platform.services import citations

    page = tmp_path / "page-3.png"
    monkeypatch.setattr(citations, "page_path", lambda *_: page)
    chunk_id, version_id, material_id, kb_id, index_id = [uuid4() for _ in range(5)]
    material = SimpleNamespace(
        id=material_id, knowledge_base_id=kb_id, title="Book", deleted_at=None, status="active"
    )
    kb = SimpleNamespace(id=kb_id, deleted_at=None)
    db = FakeDB(
        {
            (MaterialChunk, chunk_id): SimpleNamespace(text="Original quote"),
            (MaterialVersion, version_id): SimpleNamespace(
                id=version_id, material_id=material_id, version_no=2
            ),
            (Material, material_id): material,
            (KnowledgeBase, kb_id): kb,
            (IndexVersion, index_id): SimpleNamespace(status="ready"),
        }
    )
    citation = SimpleNamespace(
        id=uuid4(),
        ordinal=0,
        chunk_id=chunk_id,
        material_version_id=version_id,
        index_version_id=index_id,
        locator={"page": 3},
        quote_text=None,
        quote_sha256=None,
    )
    dto = citation_dto(db, citation)
    assert dto["quote"] == "Original quote"
    assert dto["locator"] == {"page": 3}
    assert dto["preview_url"].endswith("/preview")
    assert dto["availability"] == "available"
    assert dto["quote_status"] == "unverified"
    citation.quote_sha256 = sha256(b"Original quote").hexdigest()
    assert citation_dto(db, citation)["quote_status"] == "verified"
    db.get(MaterialChunk, chunk_id).text = "Edited quote"
    changed = citation_dto(db, citation)
    assert changed["quote"] == "" and changed["quote_status"] == "unavailable"
    assert changed["availability"] == "available" and changed["locator"] == {"page": 3}
    citation.quote_text = "Original quote"
    assert citation_dto(db, citation)["quote"] == "Original quote"
    citation.locator["ocr"] = [{"text": "Original quote"}]
    assert citation_dto(db, citation)["preview_url"].endswith("/preview")
    page.touch()
    assert citation_dto(db, citation)["preview_url"].endswith("/pages/3")
    citation.locator = {
        "table_path": [
            {"table": 2, "row": 1, "column": 3},
            {"table": 1, "row": 2, "column": 1},
        ],
        "cell_paragraph": 2,
    }
    assert citation_dto(db, citation)["location_label"] == (
        "表格 2，第 1 行，第 3 列 / 表格 1，第 2 行，第 1 列，段落 2"
    )
    citation.locator = {"heading": "1.1 子节", "start_line": 10, "end_line": 11, "level": 2}
    assert citation_dto(db, citation)["location_label"] == "1.1 子节 · 第 10 行"
    citation.locator = {"start_line": 1, "end_line": 3}
    assert citation_dto(db, citation)["location_label"] == "第 1 行"
    material.deleted_at = datetime.now(UTC)
    dto = citation_dto(db, citation)
    assert dto["availability"] == "unavailable"
    assert dto["unavailable_reason"] == "material_deleted"
    assert dto["content_url"] is None and dto["preview_url"] is None
    assert dto["quote"] == "" and dto["locator"] == {}
    assert dto["quote_status"] == "unavailable"
    material.deleted_at = None
    kb.deleted_at = datetime.now(UTC)
    citation.locator = {"image": 1, "ocr": [{"text": "Hidden OCR", "box": [[1, 2]]}]}
    dto = citation_dto(db, citation)
    assert dto["unavailable_reason"] == "knowledge_base_deleted"
    assert dto["quote"] == "" and dto["locator"] == {}
    kb.deleted_at = None
    db.rows[(IndexVersion, index_id)].status = "failed"
    assert citation_dto(db, citation)["locator"] == {}


def test_message_snapshot_scope_only_exposes_public_fields():
    run_id, kb_id = uuid4(), uuid4()
    snapshot = {
        "knowledge_base_id": str(kb_id),
        "name_snapshot": "Original name",
        "answer_mode": "knowledge_only",
        "index_version_ids": [],
        "intent": "qa",
        "secret": "private",
    }
    db = FakeDB({(AiWorkflowRun, run_id): SimpleNamespace(source_scope=snapshot)})
    message = SimpleNamespace(
        id=uuid4(),
        conversation_id=uuid4(),
        workflow_run_id=run_id,
        role="assistant",
        content="Answer",
        status="cancelled",
        sequence_no=1,
        created_at=datetime.now(UTC),
    )
    dto = message_dto(db, message)
    assert dto["turn_id"] == run_id and dto["status"] == "stopped"
    assert set(dto["scope"]) == {
        "knowledge_base_id",
        "knowledge_base_name",
        "answer_mode",
        "intent",
        "selected_material_ids",
        "index_version_ids",
    }
    assert dto["scope"]["knowledge_base_name"] == "Original name"
    assert dto["scope"]["intent"] == "qa"
    assert dto["scope"]["selected_material_ids"] is None


def test_activity_uses_shanghai_midnight_and_twenty_one_weeks():
    rows = activity_days(
        [datetime(2026, 11, 13, 16, tzinfo=UTC), datetime(2026, 11, 13, 15, 59, tzinfo=UTC)],
        date(2026, 11, 14),
    )
    assert rows[0]["date"].weekday() == 0
    assert len(rows) == 20 * 7 + date(2026, 11, 14).weekday() + 1
    assert rows[-1] == {"date": date(2026, 11, 14), "count": 1}
    assert rows[-2]["count"] == 1


def test_resource_openapi_has_explicit_response_schemas():
    app = FastAPI()
    app.include_router(router)
    schema = app.openapi()
    for path in (
        "/knowledge-bases",
        "/conversations",
        "/search",
        "/dashboard/overview",
        "/capabilities",
    ):
        assert schema["paths"][path]["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]
