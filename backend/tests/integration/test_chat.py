"""Real PostgreSQL RAG isolation and fenced-turn regressions; no provider calls."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from alembic import command
from study_platform.core.errors import ApiError
from study_platform.persistence.models import (
    AiWorkflowRun,
    Conversation,
    KnowledgeBase,
    Message,
    User,
)
from study_platform.services import chat

pytestmark = pytest.mark.integration


@pytest.fixture
def chat_db(monkeypatch):
    if os.getenv("RUN_DB_TESTS") != "1" or not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("explicit PostgreSQL test database required")
    url = make_url(os.environ["TEST_DATABASE_URL"])
    if not (url.database or "").endswith("_test") or {"dbname", "service", "servicefile"} & set(
        url.query
    ):
        pytest.fail("isolated *_test database required")
    url = url.set(drivername="postgresql+psycopg")
    name = f"study_chat_{uuid4().hex}_test"
    admin = sa.create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    created = False
    engine = None
    try:
        with admin.connect() as connection:
            if not connection.scalar(
                sa.text("SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname=current_user")
            ):
                pytest.skip("database role lacks CREATEDB")
            quoted = connection.dialect.identifier_preparer.quote(name)
            connection.exec_driver_sql(f"CREATE DATABASE {quoted}")
            created = True
        target = url.set(database=name)
        monkeypatch.setenv("MIGRATION_DATABASE_URL", target.render_as_string(hide_password=False))
        command.upgrade(Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "head")
        engine = sa.create_engine(target)
        factory = sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(chat, "session_factory", lambda: factory)
        owner, outsider, kb_a, kb_b = [uuid4() for _ in range(4)]
        with factory.begin() as db:
            for user_id in (owner, outsider):
                db.add(
                    User(
                        id=user_id,
                        email=f"{user_id}@example.test",
                        password_hash="unused",
                        display_name="Tester",
                    )
                )
            db.flush()
            db.add_all(
                [
                    KnowledgeBase(id=kb_a, owner_user_id=owner, name="A"),
                    KnowledgeBase(id=kb_b, owner_user_id=owner, name="B"),
                ]
            )
        yield SimpleNamespace(factory=factory, owner=owner, outsider=outsider, kb_a=kb_a, kb_b=kb_b)
    finally:
        if engine:
            engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.exec_driver_sql(f"DROP DATABASE {quoted}")
        admin.dispose()


def request(kb, conversation=None, content="Question"):
    return {
        "knowledge_base_id": str(kb),
        "conversation_id": str(conversation) if conversation else None,
        "content": content,
        "intent": "qa",
        "answer_mode": "knowledge_only",
        "material_ids": None,
    }


def conversation_for(state, run_id):
    with state.factory() as db:
        return db.get(AiWorkflowRun, run_id).conversation_id


def test_first_send_idempotency_and_per_conversation_concurrency(chat_db):
    s = chat_db
    data, key = request(s.kb_a), uuid4().hex
    run, fence, replay = chat.prepare(s.owner, data, key)
    conversation = conversation_for(s, run)
    with pytest.raises(ApiError) as error:
        chat.prepare(s.owner, data, key)
    assert error.value.code == "turn_in_progress"
    assert chat.checkpoint(run, fence, "资料不足", "completed", [])
    assert chat.prepare(s.owner, data, key) == (run, fence, True)
    with s.factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(Conversation)) == 1
        assert db.scalar(sa.select(sa.func.count()).select_from(Message)) == 2

    def send():
        try:
            return chat.prepare(s.owner, request(s.kb_a, conversation), uuid4().hex)
        except ApiError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: send(), range(2)))
    assert sum(isinstance(r, tuple) for r in results) == 1
    assert "conversation_busy" in results


def test_scope_switch_excludes_old_history_and_enforces_owner(chat_db):
    s = chat_db
    first, fence, _ = chat.prepare(
        s.owner, request(s.kb_a, content="Private A history"), uuid4().hex
    )
    assert chat.checkpoint(first, fence, "A answer", "completed", [])
    conv = conversation_for(s, first)
    second, _, _ = chat.prepare(s.owner, request(s.kb_b, conv), uuid4().hex)
    scope, chunks, history = chat.context(second)
    assert scope["knowledge_base_id"] == str(s.kb_b)
    assert chunks == [] and history == []
    with pytest.raises(ApiError) as error:
        chat.turn(s.outsider, conv, second)
    assert error.value.status_code == 404
    with pytest.raises(ApiError):
        chat.prepare(s.outsider, request(s.kb_a), uuid4().hex)


def test_cancel_retry_and_expired_lease_reject_stale_publication(chat_db):
    s = chat_db
    run, fence, _ = chat.prepare(s.owner, request(s.kb_a), uuid4().hex)
    conv = conversation_for(s, run)
    state = chat.cancel(s.owner, conv, run)
    assert state["status"] == "stopped"
    assert not chat.checkpoint(run, fence, "Late output", "completed", [])
    retried, new_fence, _ = chat.retry(s.owner, conv, run)
    assert retried == run and new_fence > fence
    with s.factory.begin() as db:
        db.get(AiWorkflowRun, run).lease_expires_at = chat.now() - timedelta(seconds=1)
    assert not chat.checkpoint(run, new_fence, "Expired output", "completed", [])
    state = chat.turn(s.owner, conv, run)
    assert state["status"] == "failed" and state["error_code"] == "turn_interrupted"
    assert len(state["messages"]) == 2


def test_no_evidence_stream_never_calls_cloud(chat_db, monkeypatch):
    s = chat_db

    def forbidden(*args, **kwargs):
        raise AssertionError("Provider must not be called without evidence")

    monkeypatch.setattr(chat, "embed_texts", forbidden)
    monkeypatch.setattr(chat, "chat_model", forbidden)
    run, fence, _ = chat.prepare(s.owner, request(s.kb_a), uuid4().hex)

    async def consume():
        return [event async for event in chat.stream(s.owner, run, fence)]

    events = asyncio.run(consume())
    assert any("event: done" in event for event in events)
    conv = conversation_for(s, run)
    assert chat.turn(s.owner, conv, run)["messages"][-1]["citations"] == []


def seed_evidence(s):
    from study_platform.core.config import get_settings
    from study_platform.persistence.models import (
        IndexVersion,
        Material,
        MaterialChunk,
        MaterialVersion,
    )

    material_id, version_id, index_id, chunk_id = [uuid4() for _ in range(4)]
    with s.factory.begin() as db:
        db.add(
            Material(
                id=material_id,
                owner_user_id=s.owner,
                knowledge_base_id=s.kb_a,
                title="Private evidence",
                kind="notes",
            )
        )
        db.flush()
        db.add(
            MaterialVersion(
                id=version_id,
                material_id=material_id,
                version_no=1,
                is_current=True,
                source_mime="text/plain",
                source_sha256="a" * 64,
                byte_size=10,
                object_key=str(uuid4()),
                parse_status="ready",
            )
        )
        db.flush()
        db.add(
            IndexVersion(
                id=index_id,
                material_version_id=version_id,
                generation=1,
                pipeline_profile="test",
                embedding_profile=f"{get_settings().embedding_model}:1024",
                embedding_dim=1024,
                status="ready",
                is_active=True,
                chunk_count=1,
                ready_at=chat.now(),
            )
        )
        db.flush()
        db.add(
            MaterialChunk(
                id=chunk_id,
                index_version_id=index_id,
                material_version_id=version_id,
                ordinal=0,
                text="Private evidence text",
                text_sha256="b" * 64,
                locator={"line": 1},
            )
        )
    return {
        "id": chunk_id,
        "material_version_id": version_id,
        "index_version_id": index_id,
        "material_id": material_id,
        "locator": {"line": 1},
        "text": "Private evidence text",
        "title": "Private evidence",
        "source_number": 2,
    }


def test_reference_endpoint_checks_conversation_owner(chat_db):
    from study_platform.api.v1.resources import get_source

    s = chat_db
    evidence = seed_evidence(s)
    run, fence, _ = chat.prepare(s.owner, request(s.kb_a), uuid4().hex)
    assert chat.checkpoint(run, fence, "Evidence [S2]", "completed", [evidence])
    conv = conversation_for(s, run)
    state = chat.turn(s.owner, conv, run)
    citation_id = UUID(str(state["messages"][-1]["citations"][0]["id"]))
    with s.factory() as db:
        result = get_source(citation_id, db, SimpleNamespace(id=s.owner))
        assert result["source_label"] == "S2" and result["quote"] == evidence["text"]
        with pytest.raises(ApiError) as error:
            get_source(citation_id, db, SimpleNamespace(id=s.outsider))
        assert error.value.status_code == 404


@pytest.mark.parametrize("deleted", [True, False])
def test_deleted_material_or_retired_index_cannot_publish(chat_db, deleted):
    from study_platform.persistence.models import IndexVersion, Material

    s = chat_db
    evidence = seed_evidence(s)
    run, fence, _ = chat.prepare(s.owner, request(s.kb_a), uuid4().hex)
    with s.factory.begin() as db:
        if deleted:
            db.get(Material, evidence["material_id"]).deleted_at = chat.now()
        else:
            index = db.get(IndexVersion, evidence["index_version_id"])
            index.status, index.is_active = "retired", False
    assert not chat.checkpoint(run, fence, "Stale evidence [S2]", "completed", [evidence])
    state = chat.turn(s.owner, conversation_for(s, run), run)
    assert state["status"] == "failed" and state["error_code"] == "scope_unavailable"
    assert state["messages"][-1]["citations"] == []


def test_provider_stream_failure_preserves_partial_and_error_event(chat_db, monkeypatch):
    s = chat_db
    evidence = seed_evidence(s)
    monkeypatch.setattr(chat, "retrieve", lambda chunks, content: [evidence])

    async def chunks():
        yield SimpleNamespace(content="Partial answer", usage_metadata=None)
        raise ApiError(502, "provider_test_failure", "Test failure", retryable=True)

    monkeypatch.setattr(
        chat, "chat_model", lambda: SimpleNamespace(astream=lambda prompt: chunks())
    )
    run, fence, _ = chat.prepare(s.owner, request(s.kb_a), uuid4().hex)

    async def consume():
        return [event async for event in chat.stream(s.owner, run, fence)]

    events = asyncio.run(consume())
    assert any("event: delta" in e for e in events)
    assert any("event: error" in e and "provider_test_failure" in e for e in events)
    state = chat.turn(s.owner, conversation_for(s, run), run)
    assert state["status"] == "failed"
    assert state["messages"][-1]["content"] == "Partial answer"


def test_cancel_during_actual_stream_fences_late_tokens(chat_db, monkeypatch):
    s = chat_db
    evidence = seed_evidence(s)
    monkeypatch.setattr(chat, "retrieve", lambda chunks, content: [evidence])

    async def chunks():
        yield SimpleNamespace(content="Partial ", usage_metadata=None)
        yield SimpleNamespace(content="late answer [S1]", usage_metadata=None)

    monkeypatch.setattr(
        chat, "chat_model", lambda: SimpleNamespace(astream=lambda prompt: chunks())
    )
    run, fence, _ = chat.prepare(s.owner, request(s.kb_a), uuid4().hex)
    conv = conversation_for(s, run)

    async def consume():
        events = []
        async for event in chat.stream(s.owner, run, fence):
            events.append(event)
            if "event: delta" in event and "Partial " in event:
                chat.cancel(s.owner, conv, run)
        return events

    events = asyncio.run(consume())
    assert any("event: stopped" in e for e in events)
    assert not any("event: done" in e for e in events)
    state = chat.turn(s.owner, conv, run)
    assert state["status"] == "stopped"
    assert state["messages"][-1]["content"] != "Partial late answer [S1]"
