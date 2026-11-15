"""Real PostgreSQL RAG isolation and fenced-turn regressions; no provider calls."""

import asyncio
import json
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
    from study_platform.core.config import get_settings

    settings = get_settings().model_copy(update={"rag_query_rewrite_enabled": False})
    monkeypatch.setattr(chat, "get_settings", lambda: settings)
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
    with s.factory() as db:
        assert db.get(Conversation, conv).project_knowledge_base_id == s.kb_a
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
    assert [payload(e)["phase"] for e in events if e.startswith("event: phase")] == [
        "working", "searching", "working",
    ]
    conv = conversation_for(s, run)
    assert chat.turn(s.owner, conv, run)["messages"][-1]["citations"] == []

    async def replay():
        return [e async for e in chat.stream(s.owner, run, fence, replay=True)]

    replayed = asyncio.run(replay())
    assert payload(replayed[0])["replay"] is True
    assert not any(e.startswith("event: phase") for e in replayed)


def payload(frame):
    return json.loads(frame.split("data: ", 1)[1])


@pytest.mark.parametrize("intent", ["qa", "summary"])
def test_stream_reports_phases_at_real_execution_boundaries(chat_db, monkeypatch, intent):
    s = chat_db
    evidence = seed_evidence(s)
    events = []

    def phases():
        return [e["phase"] for e in events if e["type"] == "phase"]

    def retrieve(scope, content):
        assert phases() == ["working", "searching"]
        return [evidence]

    async def chunks():
        assert phases()[-1] == "composing"
        yield SimpleNamespace(content="Answer [S1]", usage_metadata=None)

    monkeypatch.setattr(chat, "retrieve", retrieve)
    monkeypatch.setattr(chat, "chat_model", lambda: SimpleNamespace(astream=lambda _: chunks()))
    data = request(s.kb_a)
    if intent == "summary":
        data.update(intent=intent, material_ids=[str(evidence["material_id"])])
    run, fence, _ = chat.prepare(s.owner, data, uuid4().hex)

    async def consume():
        async for frame in chat.stream(s.owner, run, fence):
            events.append(payload(frame))

    asyncio.run(consume())
    expected = ["working", "searching", "composing", "working"] if intent == "qa" else [
        "working", "composing", "working",
    ]
    assert phases() == expected
    assert events[-1]["type"] == "done"
    assert events[0]["replay"] is False


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


@pytest.mark.parametrize("deleted", ["material", "knowledge_base", "index"])
def test_unavailable_ocr_citation_is_redacted_in_sources_history_turn_and_replay(chat_db, deleted):
    from study_platform.api.v1.resources import get_source, list_messages
    from study_platform.persistence.models import IndexVersion, Material

    s = chat_db
    evidence = seed_evidence(s)
    evidence["locator"] = {"page": 1, "ocr": [{"text": "Hidden OCR original", "box": [[1, 2]]}]}
    run, fence, _ = chat.prepare(s.owner, request(s.kb_a), uuid4().hex)
    assert chat.checkpoint(run, fence, "Saved answer [S2]", "completed", [evidence])
    conv = conversation_for(s, run)
    before = chat.turn(s.owner, conv, run)
    citation = before["messages"][-1]["citations"][0]
    assert citation["quote"] == evidence["text"] and citation["locator"]["ocr"]
    with s.factory.begin() as db:
        if deleted == "material":
            db.get(Material, evidence["material_id"]).deleted_at = chat.now()
        elif deleted == "knowledge_base":
            db.get(KnowledgeBase, s.kb_a).deleted_at = chat.now()
        else:
            index = db.get(IndexVersion, evidence["index_version_id"])
            index.status, index.is_active = "failed", False
    with s.factory() as db:
        source = get_source(citation["id"], db, SimpleNamespace(id=s.owner))
        history = list_messages(conv, 50, None, db, SimpleNamespace(id=s.owner))
    state = chat.turn(s.owner, conv, run)
    for item in [source, history["items"][-1]["citations"][0], state["messages"][-1]["citations"][0]]:
        assert item["quote"] == "" and item["locator"] == {}
        assert item["content_url"] is None and item["preview_url"] is None
        assert item["availability"] == "unavailable" and item["location_label"] == "第 1 页"
    assert state["messages"][-1]["content"] == "Saved answer [S2]"

    async def replay():
        return [payload(frame) async for frame in chat.stream(s.owner, run, fence, replay=True)]

    events = asyncio.run(replay())
    assert "Hidden OCR original" not in json.dumps(events)
    assert next(e for e in events if e["type"] == "sources")["citations"][0]["quote"] == ""


def test_cancel_during_rewrite_does_not_embed_or_generate(chat_db, monkeypatch):
    from study_platform.services.query_rewrite import RewriteResult

    s = chat_db
    seed_evidence(s)
    settings = chat.get_settings().model_copy(update={"rag_query_rewrite_enabled": True})
    monkeypatch.setattr(chat, "get_settings", lambda: settings)
    run, fence, _ = chat.prepare(s.owner, request(s.kb_a), uuid4().hex)
    conv = conversation_for(s, run)

    async def rewrite(_):
        chat.cancel(s.owner, conv, run)
        return RewriteResult("reworded", "rewritten", 1)

    def forbidden(*_, **__):
        raise AssertionError("cancelled turn must not call embedding or answer model")

    monkeypatch.setattr(chat, "rewrite_query", rewrite)
    monkeypatch.setattr(chat, "retrieve", forbidden)
    monkeypatch.setattr(chat, "chat_model", forbidden)

    async def consume():
        return [payload(frame) async for frame in chat.stream(s.owner, run, fence)]

    assert asyncio.run(consume())[-1]["type"] == "stopped"


def test_retrieval_keeps_scope_and_only_caps_six_for_fusion(chat_db, monkeypatch):
    from study_platform.persistence.models import ChunkEmbedding, Material, MaterialChunk

    s = chat_db
    evidence = seed_evidence(s)
    outside = seed_evidence(s)
    vector = [1.0] + [0.0] * 1023
    with s.factory.begin() as db:
        db.get(Material, outside["material_id"]).knowledge_base_id = s.kb_b
        for item in (evidence, outside):
            db.add(ChunkEmbedding(chunk_id=item["id"], embedding=vector,
                                  model_profile_id=f"{chat.get_settings().embedding_model}:1024"))
        for ordinal in range(1, 14):
            row = MaterialChunk(id=uuid4(), index_version_id=evidence["index_version_id"],
                                material_version_id=evidence["material_version_id"], ordinal=ordinal,
                                text=f"Fact {ordinal}", text_sha256="a" * 64, locator={"line": ordinal})
            db.add(row)
            db.flush()
            db.add(ChunkEmbedding(chunk_id=row.id, embedding=vector,
                                  model_profile_id=f"{chat.get_settings().embedding_model}:1024"))
    scope = {"index_version_ids": [str(evidence["index_version_id"])]}
    settings = chat.get_settings().model_copy(update={"retrieval_top_k": 12})
    monkeypatch.setattr(chat, "get_settings", lambda: settings)
    monkeypatch.setattr(chat, "embed_texts", lambda queries: [vector for _ in queries])
    baseline = chat.retrieve(scope, "Original question")
    assert len(baseline) == 12
    hits = chat.retrieve(scope, "Original question", rewritten_query="Equivalent question")
    assert len(hits) == 6 and len({row["id"] for row in hits}) == 6
    assert {row["material_version_id"] for row in hits} == {evidence["material_version_id"]}
    assert {row["material_version_id"] for row in baseline} == {evidence["material_version_id"]}
    with s.factory.begin() as db:
        db.get(Material, evidence["material_id"]).deleted_at = chat.now()
    assert chat.retrieve_vectors(scope, [vector, vector]) == []


def test_enabled_rewrite_batches_queries_answers_original_and_replay_calls_nothing(chat_db, monkeypatch):
    from unittest.mock import AsyncMock, Mock

    from study_platform.persistence.models import ChunkEmbedding
    from study_platform.services.query_rewrite import RewriteResult

    s = chat_db
    evidence = seed_evidence(s)
    vector = [1.0] + [0.0] * 1023
    with s.factory.begin() as db:
        db.add(ChunkEmbedding(chunk_id=evidence["id"], embedding=vector,
                              model_profile_id=f"{chat.get_settings().embedding_model}:1024"))
    settings = chat.get_settings().model_copy(update={"rag_query_rewrite_enabled": True})
    monkeypatch.setattr(chat, "get_settings", lambda: settings)
    rewritten = AsyncMock(return_value=RewriteResult("Equivalent question", "rewritten", 1))
    embed = Mock(return_value=[vector, vector])
    monkeypatch.setattr(chat, "rewrite_query", rewritten)
    monkeypatch.setattr(chat, "embed_texts", embed)

    async def chunks(messages):
        assert messages[-1].content == "Original question"
        yield SimpleNamespace(content="Private evidence text [S1]", usage_metadata=None)

    monkeypatch.setattr(chat, "chat_model", lambda: SimpleNamespace(astream=chunks))
    run, fence, _ = chat.prepare(s.owner, request(s.kb_a, content="Original question"), uuid4().hex)

    async def consume(replay=False):
        return [payload(frame) async for frame in chat.stream(s.owner, run, fence, replay=replay)]

    events = asyncio.run(consume())
    assert events[-1]["type"] == "done" and events[-1]["message"]["citations"]
    embed.assert_called_once_with(["Original question", "Equivalent question"])
    asyncio.run(consume(True))
    assert rewritten.call_count == 1 and embed.call_count == 1


def test_docx_repair_updates_preview_and_historical_citation_only_after_verification(chat_db, monkeypatch, tmp_path):
    from hashlib import sha256

    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    from study_platform.api.v1.materials import preview
    from study_platform.cli import repair_docx_locators
    from study_platform.persistence.models import (
        IndexVersion,
        MaterialChunk,
        MaterialSection,
        MaterialVersion,
        MessageCitation,
    )

    s = chat_db
    evidence = seed_evidence(s)
    version_id = evidence["material_version_id"]
    source = tmp_path / "source.docx"
    doc = Document()
    row = doc.add_table(rows=1, cols=2).rows[0]
    first, second = row.cells
    text = "Original evidence"
    second.text = text
    row._tr.remove(first._tc)
    before = OxmlElement("w:gridBefore")
    row._tr.get_or_add_trPr().append(before)
    malformed_source = tmp_path / "missing-grid-value.docx"
    doc.save(malformed_source)
    before.set(qn("w:val"), "1")
    doc.save(source)
    original = source.read_bytes()
    old = {"table_path": [{"table": 1, "row": 1, "column": 1}], "cell_paragraph": 1}
    corrected = {"table_path": [{"table": 1, "row": 1, "column": 2}], "cell_paragraph": 1}
    digest = sha256(text.encode()).hexdigest()
    evidence.update(text=text, locator=old)
    malformed_id = UUID(int=1)  # Checked before the valid version.
    sources = {malformed_id: malformed_source, version_id: source}
    monkeypatch.setattr(repair_docx_locators, "source_path", sources.__getitem__)
    monkeypatch.setattr("study_platform.services.citations.source_path", lambda _: source)
    with s.factory.begin() as db:
        version = db.get(MaterialVersion, version_id)
        version.version_no = 2
        version.source_mime = repair_docx_locators.DOCX
        version.source_sha256 = sha256(original).hexdigest()
        version.parser_profile = "document-v1"
        db.flush()
        malformed = malformed_source.read_bytes()
        db.add(
            MaterialVersion(
                id=malformed_id,
                material_id=version.material_id,
                version_no=1,
                is_current=False,
                source_mime=repair_docx_locators.DOCX,
                source_sha256=sha256(malformed).hexdigest(),
                byte_size=len(malformed),
                object_key=str(malformed_id),
                parse_status="ready",
                parser_profile="document-v1",
            )
        )
        index = db.get(IndexVersion, evidence["index_version_id"])
        index.pipeline_profile = "document-v1"
        section = MaterialSection(id=uuid4(), material_version_id=version_id, ordinal=0, level=1,
                                  text=text, locator=old)
        db.add(section)
        db.flush()
        chunk = db.get(MaterialChunk, evidence["id"])
        chunk.section_id, chunk.text, chunk.text_sha256, chunk.locator = section.id, text, digest, old
    run, fence, _ = chat.prepare(s.owner, request(s.kb_a), uuid4().hex)
    assert chat.checkpoint(run, fence, "Original answer [S2]", "completed", [evidence])
    conv = conversation_for(s, run)
    with s.factory.begin() as db:
        citation = db.scalar(sa.select(MessageCitation))
        citation_id = citation.id
        citation.quote_sha256 = "a" * 64
    assert repair_docx_locators.repair(s.factory, apply=True)["skipped"] == 2
    with s.factory.begin() as db:
        assert db.get(MaterialChunk, evidence["id"]).locator == old
        assert db.scalar(sa.select(MaterialSection)).locator == old
        db.get(MessageCitation, citation_id).quote_sha256 = digest

    expected = {"checked": 2, "changed_versions": 1, "changed_rows": 3, "skipped": 1}
    assert repair_docx_locators.repair(s.factory) == expected
    with s.factory() as db:
        assert preview(version_id, db, SimpleNamespace(id=s.owner))["sections"][0]["locator"] == old
    assert repair_docx_locators.repair(s.factory, apply=True) == expected
    assert repair_docx_locators.repair(s.factory, apply=True)["changed_rows"] == 0
    with s.factory() as db:
        assert preview(version_id, db, SimpleNamespace(id=s.owner))["sections"] == [{"text": text, "locator": corrected}]
        chunk = db.get(MaterialChunk, evidence["id"])
        assert chunk.locator == corrected and chunk.text_sha256 == digest
        assert chunk.index_version_id == evidence["index_version_id"]
        assert db.get(MessageCitation, citation_id).quote_sha256 == digest
    saved = chat.turn(s.owner, conv, run)["messages"][-1]
    assert saved["content"] == "Original answer [S2]"
    assert saved["citations"][0]["location_label"] == "表格 1，第 1 行，第 2 列，段落 1"
    assert saved["citations"][0]["quote"] == text and source.read_bytes() == original


@pytest.mark.parametrize("intent", ["summary", "empty"])
def test_summary_and_empty_library_skip_rewrite_even_when_enabled(chat_db, monkeypatch, intent):
    s = chat_db
    settings = chat.get_settings().model_copy(update={"rag_query_rewrite_enabled": True})
    monkeypatch.setattr(chat, "get_settings", lambda: settings)

    def forbidden(*_, **__):
        raise AssertionError("this path must not rewrite or embed a query")

    monkeypatch.setattr(chat, "rewrite_query", forbidden)
    monkeypatch.setattr(chat, "embed_texts", forbidden)
    data = request(s.kb_a)
    if intent == "summary":
        evidence = seed_evidence(s)
        data.update(intent="summary", material_ids=[str(evidence["material_id"])])

    async def answer(_):
        yield SimpleNamespace(content="Summary [S1]", usage_metadata=None)

    monkeypatch.setattr(chat, "chat_model", lambda: SimpleNamespace(astream=answer))
    run, fence, _ = chat.prepare(s.owner, data, uuid4().hex)

    async def consume():
        return [payload(frame) async for frame in chat.stream(s.owner, run, fence)]

    events = asyncio.run(consume())
    assert events[-1]["type"] == "done"


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
