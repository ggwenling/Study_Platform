"""Database-backed ownership, paging and soft-delete resource checks."""

import os
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from study_platform.api.v1.auth import router as auth_router
from study_platform.api.v1.library_detail import router as library_router
from study_platform.api.v1.materials import router as material_router
from study_platform.api.v1.resources import router
from study_platform.api.v1.studio import router as studio_router
from study_platform.core.config import Settings, get_settings
from study_platform.core.errors import ApiError, api_error_handler
from study_platform.persistence.database import get_db
from study_platform.persistence.models import (
    AiWorkflowRun,
    Flashcard,
    FlashcardDeck,
    IndexVersion,
    KnowledgeBase,
    Material,
    MaterialChunk,
    MaterialVersion,
    Message,
)
from study_platform.services import auth_limits
from study_platform.services.chat import chunk_query
from study_platform.services.learning_anchor import ensure_course_anchor
from study_platform.services.storage import page_path, source_path

pytestmark = pytest.mark.integration


@pytest.fixture
def resource_client(monkeypatch):
    if os.getenv("RUN_DB_TESTS") != "1" or not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("explicit PostgreSQL test database required")
    url = make_url(os.environ["TEST_DATABASE_URL"])
    if not (url.database or "").endswith("_test") or {"dbname", "service", "servicefile"} & set(
        url.query
    ):
        pytest.fail("isolated *_test database required")
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    monkeypatch.setattr(auth_limits, "_buckets", defaultdict(list))
    with engine.connect() as connection:
        tx = connection.begin()
        db = Session(connection, join_transaction_mode="create_savepoint")
        app = FastAPI()
        app.state.settings = Settings(
            _env_file=None, auth_jwt_secret="resource-test-key" * 3,
            deepseek_api_key="", embedding_api_key="",
        )
        app.include_router(auth_router)
        app.include_router(router)
        app.include_router(library_router)
        app.include_router(material_router)
        app.include_router(studio_router)
        app.add_exception_handler(ApiError, api_error_handler)
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app, headers={"Origin": "http://localhost:5173"}) as http:
            yield http, db
        db.close()
        tx.rollback()
    engine.dispose()


def signup(http):
    result = http.post(
        "/auth/register",
        json={
            "email": f"{uuid4().hex}@example.test",
            "password": "safe-password-123",
            "display_name": "Resource tester",
        },
    )
    assert result.status_code == 201
    http.headers["Authorization"] = "Bearer " + result.json()["access_token"]
    return UUID(result.json()["user"]["id"])


def test_reader_keeps_historical_index_context_and_caches_pdf_pages(resource_client, tmp_path, monkeypatch):
    http, db = resource_client
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        owner = signup(http)
        library = UUID(http.post("/knowledge-bases", json={"name": "Reader"}).json()["id"])
        document = Material(owner_user_id=owner, knowledge_base_id=library, title="Pages.pdf", kind="other")
        db.add(document)
        db.flush()
        version = MaterialVersion(
            id=uuid4(), material_id=document.id, version_no=1, is_current=True,
            source_mime="application/pdf", object_key=str(uuid4()), parse_status="ready",
            source_sha256="a" * 64, byte_size=1, page_count=3,
        )
        db.add(version)
        db.flush()
        history, active = [
            IndexVersion(
                material_version_id=version.id, generation=generation, status="ready" if is_active else "retired",
                is_active=is_active, pipeline_profile="test", embedding_profile="test",
                ready_at=datetime.now(UTC),
            )
            for generation, is_active in [(1, False), (2, True)]
        ]
        db.add_all([history, active])
        db.flush()
        chunks = [
            MaterialChunk(
                material_version_id=version.id, index_version_id=index.id, ordinal=ordinal,
                text=f"generation {index.generation} chunk {ordinal}", text_sha256="b" * 64,
                locator={"page": min(ordinal + 1, 3)},
            )
            for index, count in [(history, 5), (active, 1)]
            for ordinal in range(count)
        ]
        db.add_all(chunks)
        db.commit()
        path = source_path(version.id)
        path.parent.mkdir()
        with Image.new("RGB", (200, 100), "white") as image:
            image.save(path, format="PDF", save_all=True, append_images=[image, image])
        base = f"/material-versions/{version.id}"
        assert http.get(f"{base}/reader").json()["target_chunk_id"] == str(chunks[-1].id)
        response = http.get(f"{base}/reader", params={"chunk_id": str(chunks[2].id), "window": 1})
        assert response.status_code == 200, response.text
        context = response.json()
        assert context["target_page"] == 3 and context["can_render_pages"]
        assert context["total_chunks"] == 5 and context["has_prev"] and context["has_next"]
        assert [row["ordinal"] for row in context["chunks"]] == [1, 2, 3]
        assert [row["is_target"] for row in context["chunks"]] == [False, True, False]
        assert context["target_quote"] == chunks[2].text
        assert http.get(f"{base}/reader", params={"chunk_id": str(uuid4())}).status_code == 404

        assert not page_path(version.id, 2).is_file()
        rendered = http.get(f"{base}/pages/2")
        assert rendered.status_code == 200 and rendered.headers["content-type"] == "image/png"
        assert rendered.content == page_path(version.id, 2).read_bytes()
        path.unlink()
        assert http.get(f"{base}/pages/2").content == rendered.content
        assert http.get(f"{base}/pages/4").status_code == 404
        signup(http)
        assert http.get(f"{base}/reader").status_code == 404
        assert http.get(f"{base}/pages/2").status_code == 404
    finally:
        get_settings.cache_clear()


def test_due_flashcards_across_decks_and_dashboard(resource_client):
    http, db = resource_client
    now = datetime.now(UTC)
    owner = signup(http)

    def deck(title, user=owner, *, deleted_at=None):
        kb_id = UUID(http.post("/knowledge-bases", json={"name": title}).json()["id"])
        ensure_course_anchor(db, db.get(KnowledgeBase, kb_id))
        row = FlashcardDeck(owner_user_id=user, course_id=kb_id, title=title,
                            source_scope={}, deleted_at=deleted_at)
        db.add(row)
        db.commit()
        return row

    first = deck("First")
    second = deck("Second")
    deleted = deck("Removed", deleted_at=now)
    owner_headers = dict(http.headers)
    foreign = deck("Private", signup(http))
    http.headers.update(owner_headers)
    db.add_all([
        Flashcard(deck_id=first.id, front="Older", back="Answer", due_at=now - timedelta(days=2)),
        Flashcard(deck_id=second.id, front="Newer", back="Answer", due_at=now - timedelta(days=1)),
        Flashcard(deck_id=first.id, front="Future", back="Answer", due_at=now + timedelta(days=1)),
        Flashcard(deck_id=first.id, front="Paused", back="Answer", due_at=now, status="suspended"),
        Flashcard(deck_id=deleted.id, front="Removed", back="Answer", due_at=now),
        Flashcard(deck_id=foreign.id, front="Private", back="Answer", due_at=now),
    ])
    db.commit()

    due = http.get("/flashcards/due", params={"limit": 1}).json()
    assert due["total_due"] == 2
    assert [card["front"] for card in due["items"]] == ["Older"]
    assert due["items"][0]["deck_title"] == due["items"][0]["knowledge_base_name"] == "First"
    filtered = http.get("/flashcards/due", params={"knowledge_base_id": second.course_id}).json()
    assert filtered["total_due"] == 1 and filtered["items"][0]["front"] == "Newer"
    assert http.get("/flashcards/due", params={"knowledge_base_id": foreign.course_id}).status_code == 404
    overview = http.get("/dashboard/overview").json()
    assert overview["due_flashcard_count"] == 2
    assert overview["due_wrong_question_count"] == 0


def test_project_conversation_ownership_paging_and_library_deletion(resource_client):
    http, _ = resource_client
    signup(http)
    kb = http.post("/knowledge-bases", json={"name": "Project"}).json()["id"]
    other = http.post("/knowledge-bases", json={"name": "Other"}).json()["id"]
    first = http.post("/conversations", json={"project_knowledge_base_id": kb}).json()
    second = http.post("/conversations", json={"project_knowledge_base_id": kb}).json()
    outside = http.post("/conversations", json={"project_knowledge_base_id": other}).json()
    params = {"project_knowledge_base_id": kb, "limit": 1}
    page = http.get("/conversations", params=params).json()
    assert len(page["items"]) == 1 and page["next_cursor"]
    assert page["items"][0]["project_knowledge_base_id"] == kb
    next_page = http.get("/conversations", params={**params, "cursor": page["next_cursor"]}).json()
    assert {page["items"][0]["id"], next_page["items"][0]["id"]} == {first["id"], second["id"]}
    assert http.get("/conversations", params={**params, "cursor": outside["id"]}).status_code == 400
    owner_headers = dict(http.headers)
    signup(http)
    assert http.post("/conversations", json={"project_knowledge_base_id": kb}).status_code == 404
    assert http.get("/conversations", params=params).status_code == 404
    http.headers.update(owner_headers)
    assert http.delete(f"/knowledge-bases/{kb}").status_code == 204
    assert http.get(f'/conversations/{first["id"]}').json()["project_knowledge_base_id"] is None


def test_conversation_archive_is_reversible_and_keeps_project_and_messages(resource_client):
    http, db = resource_client
    signup(http)
    kb = http.post("/knowledge-bases", json={"name": "Archive project"}).json()["id"]
    row = http.post("/conversations", json={"title": "Archive example", "project_knowledge_base_id": kb}).json()
    path = f'/conversations/{row["id"]}'
    db.add(Message(conversation_id=UUID(row["id"]), role="user", content="Keep this message",
                   status="completed", sequence_no=0))
    db.commit()
    owner_headers = dict(http.headers)
    signup(http)
    assert http.patch(f"{path}/archive", json={"archived": True}).status_code == 404
    assert http.get("/conversations", params={"archived": True}).json()["items"] == []
    http.headers.update(owner_headers)
    archived = http.patch(f"{path}/archive", json={"archived": True})
    assert archived.status_code == 200
    data = archived.json()
    assert data["archived_at"] and data["updated_at"] == row["updated_at"]
    assert http.patch(f"{path}/archive", json={"archived": True}).json()["archived_at"] == data["archived_at"]
    assert http.get("/conversations").json()["items"] == []
    assert http.get("/conversations", params={"project_knowledge_base_id": kb}).json()["items"] == []
    assert http.get("/conversations", params={"archived": True}).json()["items"] == [data]
    assert http.get(f"{path}/messages").json()["items"][0]["content"] == "Keep this message"
    assert http.patch(f"{path}/archive", json={"archived": False}).json()["archived_at"] is None
    assert http.get("/conversations", params={"project_knowledge_base_id": kb}).json()["items"] == [row]


def test_resources_ownership_pagination_and_tombstones(resource_client):
    http, db = resource_client
    owner = signup(http)
    a = http.post("/knowledge-bases", json={"name": "Alpha"}).json()
    b = http.post("/knowledge-bases", json={"name": "Beta"}).json()
    page = http.get("/knowledge-bases", params={"limit": 1}).json()
    assert len(page["items"]) == 1 and page["next_cursor"]
    second = http.get("/knowledge-bases", params={"limit": 1, "cursor": page["next_cursor"]}).json()
    assert second["items"][0]["id"] != page["items"][0]["id"]
    conversation = http.post("/conversations", json={"title": "Alpha conversation"}).json()
    run = AiWorkflowRun(
        id=uuid4(),
        owner_user_id=owner,
        conversation_id=UUID(conversation["id"]),
        knowledge_base_id=UUID(a["id"]),
        status="running",
        fencing=4,
        source_scope={
            "intent": "qa",
            "knowledge_base_id": a["id"],
            "name_snapshot": "Alpha",
            "answer_mode": "knowledge_only",
            "index_version_ids": [],
        },
    )
    db.add(run)
    db.flush()
    message = Message(
        id=uuid4(),
        conversation_id=UUID(conversation["id"]),
        role="assistant",
        content="Partial",
        status="streaming",
        sequence_no=0,
        workflow_run_id=run.id,
    )
    db.add(message)
    db.commit()
    assert (
        http.get(f"/conversations/{conversation['id']}/messages").json()["items"][0]["scope"][
            "knowledge_base_name"
        ]
        == "Alpha"
    )
    assert len(http.get("/search", params={"q": "Alpha"}).json()["items"]) == 2
    assert http.delete(f"/conversations/{conversation['id']}").status_code == 204
    db.refresh(run)
    db.refresh(message)
    assert run.status == "cancelled" and run.fencing == 5
    assert message.status == "cancelled"
    assert http.get(f"/conversations/{conversation['id']}").status_code == 404
    assert http.delete(f"/knowledge-bases/{a['id']}").status_code == 204
    assert http.get(f"/knowledge-bases/{a['id']}").status_code == 404
    signup(http)
    assert http.get(f"/knowledge-bases/{b['id']}").status_code == 404
    assert http.get("/knowledge-bases").json()["items"] == []
    assert http.get("/dashboard/overview").json()["knowledge_base_count"] == 0


def test_content_search_uses_current_owned_fragments_and_notes_without_models(resource_client):
    http, db = resource_client

    def library(name):
        return http.post("/knowledge-bases", json={"name": name}).json()["id"]

    def note(kb, title, body):
        response = http.post(f"/knowledge-bases/{kb}/notes", json={"title": title, "content_md": body})
        assert response.status_code == 201
        return response.json()["id"]

    def fragment(material, *, current=True, active=True, enabled=True):
        version = MaterialVersion(
            id=uuid4(), material_id=material.id, version_no=1 if current else 2, is_current=current,
            source_mime="application/pdf", object_key=str(uuid4()), parse_status="ready",
            source_sha256="a" * 64, byte_size=1,
        )
        db.add(version)
        db.flush()
        index = IndexVersion(
            id=uuid4(), material_version_id=version.id, generation=1, status="ready", is_active=active,
            pipeline_profile="test", embedding_profile="test:1024", chunk_count=1, ready_at=datetime.now(UTC),
        )
        db.add(index)
        db.flush()
        chunk = MaterialChunk(
            id=uuid4(), material_version_id=version.id, index_version_id=index.id, ordinal=0,
            text=body, text_sha256="b" * 64, retrieval_enabled=enabled, locator={"page": 7, "ocr": []},
        )
        db.add(chunk)
        db.commit()
        return chunk

    def material(owner, kb, title):
        row = Material(id=uuid4(), owner_user_id=owner, knowledge_base_id=UUID(kb), title=title, kind="other")
        db.add(row)
        db.flush()
        return row

    def search(query, **params):
        response = http.get("/search", params={"q": query, "include_content": "true", **params})
        assert response.status_code == 200, response.text
        return response.json()

    body = "文档说明。" * 25 + "拉格朗日乘子法 Newton REF_2026%" + "补充背景。" * 25
    owner = signup(http)
    owner_headers = dict(http.headers)
    kb = library("分析原件")
    notes_kb = library("研究日志")
    document = material(owner, kb, "计算讲义.pdf")
    hit = fragment(document, enabled=False)
    archived = fragment(document, current=False)
    inactive = fragment(material(owner, kb, "未发布.pdf"), active=False)
    note_id = note(notes_kb, "实验结果", body)
    note(notes_kb, "搜索用反例", "REFX2026X")
    title_note = note(notes_kb, "只出现在标题里的词", "其他内容")

    another_user = signup(http)
    private_kb = library("另一个用户")
    private_hit = fragment(material(another_user, private_kb, "私有.pdf"))
    private_note = note(private_kb, "私有笔记", body)
    assert {row["id"] for row in search("Newton")["items"]} == {
        str(private_hit.id), private_note,
    }
    assert http.get(f"/knowledge-bases/{kb}/fragments/{hit.id}").status_code == 404
    assert http.get(f"/notes/{note_id}").status_code == 404

    http.headers.update(owner_headers)
    for query in ("拉格朗日乘子法", "newton", "REF_2026%"):
        items = search(query)["items"]
        assert {row["id"] for row in items} == {str(hit.id), note_id}
        assert all(query.lower() in row["excerpt"].lower() and len(row["excerpt"]) < len(body) for row in items)
    page = search("newton", limit=1)
    last = search("newton", limit=1, cursor=page["next_cursor"])
    assert last["next_cursor"] is None
    hits = {row["type"]: row for row in page["items"] + last["items"]}
    assert hits["fragment"]["locator"] == {"page": 7}
    assert hits["fragment"]["retrieval_enabled"] is False
    assert hits["note"]["knowledge_base_id"] == notes_kb
    assert search("只出现在标题里的词")["items"][0]["id"] == title_note
    assert http.get("/search", params={"q": "newton"}).json()["items"] == []
    assert http.get("/search", params={"q": "计算讲义"}).json()["items"] == [
        {"id": str(document.id), "type": "material", "title": "计算讲义.pdf", "knowledge_base_id": kb},
    ]

    fragment_url = f"/knowledge-bases/{kb}/fragments/{hit.id}"
    assert http.get(fragment_url).json()["text"] == body
    assert http.get(f"/knowledge-bases/{kb}/fragments/{archived.id}").status_code == 404
    assert http.get(f"/knowledge-bases/{kb}/fragments/{inactive.id}").status_code == 404
    assert http.get(f"/knowledge-bases/{notes_kb}/fragments/{hit.id}").status_code == 404
    assert http.get(f"/notes/{note_id}").json()["content_md"] == body
    assert db.scalars(chunk_query([hit.index_version_id], owner)).all() == []

    assert http.delete(f"/notes/{note_id}").status_code == 204
    assert http.get(f"/notes/{note_id}").status_code == 404
    document.deleted_at = datetime.now(UTC)
    db.commit()
    assert http.get(fragment_url).status_code == 404
    assert search("newton")["items"] == []
    assert http.delete(f"/knowledge-bases/{notes_kb}").status_code == 204
    assert search("只出现在标题里的词")["items"] == []
