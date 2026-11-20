"""Learning flow: service integration + authenticated wrong-book API."""

import hashlib
import io
import os
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from study_platform.core.config import Settings
from study_platform.core.config import get_settings as load_settings
from study_platform.core.errors import ApiError
from study_platform.main import create_app
from study_platform.persistence.database import get_db
from study_platform.persistence.models import (
    ChunkEmbedding,
    Course,
    IndexVersion,
    KnowledgeBase,
    Material,
    MaterialChunk,
    MaterialVersion,
    User,
)
from study_platform.services import chat, learning, model_calls, model_connections

pytestmark = pytest.mark.integration


@pytest.fixture
def learning_env(tmp_path, monkeypatch):
    if os.getenv("RUN_DB_TESTS") != "1" or not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("explicit PostgreSQL test database required")
    url = make_url(os.environ["TEST_DATABASE_URL"])
    if not (url.database or "").endswith("_test"):
        pytest.fail("isolated *_test database required")
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    load_settings.cache_clear()
    settings = Settings(
        _env_file=None,
        auth_jwt_secret="learning-test-key" * 3,
        storage_dir=tmp_path,
        learning_stub_grader=True,
        deepseek_api_key="",
        embedding_model="qwen3.7-text-embedding-flash",
        database_url=url.render_as_string(hide_password=False),
        auth_public_url="http://testserver",
        allowed_origins=["http://testserver"],
    )
    for target in (
        "study_platform.services.learning_grade.get_settings",
        "study_platform.services.learning.get_settings",
        "study_platform.services.chat.get_settings",
        "study_platform.api.v1.learning.get_settings",
        "study_platform.core.config.get_settings",
    ):
        monkeypatch.setattr(target, lambda: settings)
    monkeypatch.setattr(chat, "embed_texts", lambda texts: [[0.05] * 1024 for _ in texts])

    owner = uuid4()
    kb_id = uuid4()
    with engine.connect() as connection:
        tx = connection.begin()
        db = Session(connection, join_transaction_mode="create_savepoint")
        factory = sessionmaker(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        monkeypatch.setattr(chat, "session_factory", lambda: factory)
        monkeypatch.setattr("study_platform.services.model_connections.session_factory", lambda: factory)
        db.add(
            User(
                id=owner,
                email=f"{owner.hex}@example.test",
                password_hash="x",
                display_name="Learner",
            )
        )
        db.flush()
        db.add(KnowledgeBase(id=kb_id, owner_user_id=owner, name="Learn"))
        db.add(Course(id=kb_id, owner_user_id=owner, name="Learn"))
        db.flush()
        material = Material(
            id=uuid4(),
            owner_user_id=owner,
            knowledge_base_id=kb_id,
            course_id=kb_id,
            title="Doc",
            kind="other",
            status="active",
        )
        version = MaterialVersion(
            id=uuid4(),
            material_id=material.id,
            version_no=1,
            is_current=True,
            source_mime="text/plain",
            source_sha256=hashlib.sha256(b"body").hexdigest(),
            byte_size=4,
            object_key=str(uuid4()),
            parse_status="ready",
        )
        index = IndexVersion(
            id=uuid4(),
            material_version_id=version.id,
            generation=1,
            pipeline_profile="test",
            embedding_profile="qwen3.7-text-embedding-flash:1024",
            status="ready",
            is_active=True,
            chunk_count=1,
            ready_at=datetime.now(UTC),
        )
        chunk = MaterialChunk(
            id=uuid4(),
            index_version_id=index.id,
            material_version_id=version.id,
            ordinal=0,
            text="第1题：光速大约是多少？答案300000",
            text_sha256=hashlib.sha256(b"t").hexdigest(),
            locator={},
        )
        db.add(material)
        db.flush()
        db.add(version)
        db.flush()
        db.add(index)
        db.add(chunk)
        db.flush()
        db.add(
            ChunkEmbedding(
                chunk_id=chunk.id,
                model_profile_id="qwen3.7-text-embedding-flash:1024",
                embedding=[0.05] * 1024,
            )
        )
        db.flush()
        app = create_app(settings)
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(
            app,
            base_url="http://testserver/api/v1",
            headers={"Origin": "http://testserver"},
        ) as http:
            yield http, db, owner, kb_id
        db.close()
        tx.rollback()
    engine.dispose()


def _png_with_text(text: str) -> bytes:
    img = Image.new("RGB", (320, 100), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 30), text, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_learning_flow(learning_env):
    http, db, owner, kb_id = learning_env
    from study_platform.services.chat import visible_indices

    assert visible_indices(db, owner, kb_id)
    quiz = learning.generate_learning_quiz(
        db, owner_user_id=owner, knowledge_base_id=kb_id, count=1
    )
    attempt = learning.start_attempt(db, owner_user_id=owner, quiz_id=quiz.id)
    payload = learning.attempt_payload(db, attempt.id, owner)
    item = payload["items"][0]
    assert "reference_answer" not in item
    img = _png_with_text("300000")
    row = learning.submit_image_answer(
        db, owner_user_id=owner, response_id=UUID(item["response_id"]), image_bytes=img
    )
    assert row.verdict in {"correct", "pending", "incorrect"}
    revealed = learning.attempt_payload(db, attempt.id, owner)["items"][0]
    assert revealed.get("reference_answer")
    learning.override_verdict(
        db, owner_user_id=owner, response_id=row.id, verdict="incorrect", reference_answer=None
    )
    assert learning.list_wrong_questions(db, owner)


def test_wrong_book_api_requires_auth(learning_env):
    http, db, owner, kb_id = learning_env
    assert http.get("/learning/wrong-questions").status_code == 401


def test_learning_v2_mcq_text_answer_history_and_wrong_retry(learning_env):
    _, db, owner, kb_id = learning_env
    quiz = learning.generate_learning_quiz(
        db, owner_user_id=owner, knowledge_base_id=kb_id, count=1, mode="mcq"
    )
    attempt = learning.start_attempt(db, owner_user_id=owner, quiz_id=quiz.id)
    payload = learning.attempt_payload(db, attempt.id, owner)
    item = payload["items"][0]
    assert item["item_type"] == "mcq"
    assert len(item["options"]) == 4
    assert item["sources"] and item["sources"][0]["quote"] is None

    # Answer with a deliberately wrong letter first to populate wrong-book
    first_resp = learning.submit_text_answer(
        db, owner_user_id=owner, response_id=UUID(item["response_id"]), answer_text="D"
    )
    if first_resp.verdict == "correct":
        first_resp = learning.submit_text_answer(
            db, owner_user_id=owner, response_id=UUID(item["response_id"]), answer_text="B"
        )
    assert first_resp.verdict == "incorrect"

    history = learning.list_attempts(db, owner, knowledge_base_id=kb_id)
    assert history and history[0]["answered_count"] == 1

    wrongs = learning.list_wrong_questions(db, owner, knowledge_base_id=kb_id)
    assert len(wrongs) == 1
    assert wrongs[0]["status"] == "open"
    assert wrongs[0]["is_due"] is True
    assert datetime.fromisoformat(wrongs[0]["next_review_at"]) <= datetime.now(UTC)
    assert wrongs[0]["sources"] and wrongs[0]["sources"][0]["quote"]

    # Retry wrong question and answer correctly -> original wrong question becomes reviewing
    retry_attempt = learning.retry_wrong_questions(
        db, owner_user_id=owner, wrong_ids=[UUID(wrongs[0]["id"])], knowledge_base_id=kb_id
    )
    retry_payload = learning.attempt_payload(db, retry_attempt.id, owner)
    retry_item = retry_payload["items"][0]
    correct_answer = wrongs[0]["reference_answer"]
    retry_resp = learning.submit_text_answer(
        db, owner_user_id=owner, response_id=UUID(retry_item["response_id"]), answer_text=correct_answer
    )
    assert retry_resp.verdict == "correct"

    updated_wrongs = learning.list_wrong_questions(db, owner, knowledge_base_id=kb_id)
    assert len(updated_wrongs) == 1
    assert updated_wrongs[0]["status"] == "reviewing"
    scheduled = datetime.fromisoformat(updated_wrongs[0]["next_review_at"])
    assert scheduled > datetime.now(UTC)
    assert updated_wrongs[0]["is_due"] is False


def test_studio_notes_artifacts_ingest_and_flashcard_sm2_review(learning_env):
    from types import SimpleNamespace

    from study_platform.services import studio

    _, db, owner, kb_id = learning_env
    user = SimpleNamespace(id=owner)

    note = studio.create_note(
        db,
        owner_user_id=owner,
        knowledge_base_id=kb_id,
        title="线性变换总结",
        content_md="线性变换保持加法与数乘。",
    )
    assert note["edit_revision"] == 0

    updated = studio.update_note(
        db,
        owner_user_id=owner,
        note_id=UUID(note["id"]),
        title="线性变换核心总结",
        content_md="线性变换保持向量加法与标量乘法。",
    )
    assert updated["edit_revision"] == 1
    assert updated["title"] == "线性变换核心总结"

    ingest_res = studio.ingest_note_to_library(db, user=user, note_id=UUID(note["id"]))
    assert ingest_res["status"] == "queued"
    reloaded = studio.get_note(db, owner, UUID(note["id"]))
    assert reloaded["ingested_material_id"] == ingest_res["material_id"]

    guide = studio.generate_studio_artifact(
        db, owner_user_id=owner, knowledge_base_id=kb_id, kind="study_guide"
    )
    mindmap = studio.generate_studio_artifact(
        db, owner_user_id=owner, knowledge_base_id=kb_id, kind="mindmap"
    )
    assert guide["artifact_kind"] == "study_guide" and guide["citations"]
    assert "## 知识层级树" in mindmap["content_md"]

    deck = studio.generate_flashcard_deck(
        db, owner_user_id=owner, knowledge_base_id=kb_id, count=3
    )
    assert deck["card_count"] >= 1
    card_id = UUID(deck["cards"][0]["id"])

    reviewed_good = studio.review_flashcard(db, owner_user_id=owner, card_id=card_id, rating=3)
    assert reviewed_good["repetition_count"] == 1
    assert reviewed_good["interval_days"] >= 2
    assert reviewed_good["ease_factor"] == 2.65

    reviewed_again = studio.review_flashcard(db, owner_user_id=owner, card_id=card_id, rating=0)
    assert reviewed_again["repetition_count"] == 0
    assert reviewed_again["interval_days"] == 0



def test_studio_citation_ordinals_and_flashcard_idempotency(learning_env, monkeypatch):
    from types import SimpleNamespace

    from study_platform.core.errors import ApiError
    from study_platform.persistence.models import Conversation, Message, MessageCitation
    from study_platform.services import studio

    _, db, owner, kb_id = learning_env

    # Add two more chunks (ordinal 1 and 2) so we have 3 distinct chunks in the index
    first_chunk = db.scalar(
        sa.select(MaterialChunk)
        .join(MaterialVersion, MaterialVersion.id == MaterialChunk.material_version_id)
        .join(Material, Material.id == MaterialVersion.material_id)
        .where(Material.owner_user_id == owner, Material.knowledge_base_id == kb_id)
    )
    extra_chunks = []
    for ord_no, txt in ((1, "第二段证据片段"), (2, "第三段证据片段")):
        ch = MaterialChunk(
            id=uuid4(),
            index_version_id=first_chunk.index_version_id,
            material_version_id=first_chunk.material_version_id,
            ordinal=ord_no,
            text=txt,
            text_sha256=hashlib.sha256(txt.encode("utf-8")).hexdigest(),
            locator={"page": ord_no + 1},
        )
        db.add(ch)
        db.flush()
        extra_chunks.append(ch)
    all_chunks = [first_chunk, *extra_chunks]

    # 1. Message with sparse citations [S2] (ordinal=1) and [S4] (ordinal=3)
    conv = Conversation(id=uuid4(), owner_user_id=owner, title="引文测试")
    db.add(conv)
    db.flush()
    msg = Message(
        id=uuid4(),
        conversation_id=conv.id,
        role="assistant",
        content="回答仅引用了 [S2] 与 [S4]。",
        status="completed",
        sequence_no=1,
    )
    db.add(msg)
    db.flush()
    for ord_idx, target_chunk in ((1, all_chunks[1]), (3, all_chunks[2])):
        db.add(
            MessageCitation(
                message_id=msg.id,
                chunk_id=target_chunk.id,
                material_version_id=target_chunk.material_version_id,
                index_version_id=target_chunk.index_version_id,
                locator=target_chunk.locator,
                quote_sha256=hashlib.sha256(target_chunk.text.encode("utf-8")).hexdigest(),
                ordinal=ord_idx,
            )
        )
    db.flush()

    saved_note = studio.create_note(
        db,
        owner_user_id=owner,
        knowledge_base_id=kb_id,
        title="稀疏引文笔记",
        content_md=msg.content,
        message_id=msg.id,
    )
    assert [c["source_label"] for c in saved_note["citations"]] == ["S2", "S4"]
    assert saved_note["citations"][0]["quote"] == "第二段证据片段"
    assert saved_note["citations"][1]["quote"] == "第三段证据片段"

    # 2. Studio artifact where LLM cites [S3] first, then [S1] (skipping [S2])
    llm_settings = studio.get_settings().model_copy(update={"learning_stub_grader": False})
    monkeypatch.setattr(studio, "get_settings", lambda: llm_settings)
    monkeypatch.setattr(studio, "llm_available", lambda _uid: True)
    monkeypatch.setattr(
        studio,
        "invoke_chat",
        lambda *_a, **_kw: SimpleNamespace(content="## 核心主题概览\n先引 [S3]，后引 [S1]。"),
    )
    artifact = studio.generate_studio_artifact(
        db, owner_user_id=owner, knowledge_base_id=kb_id, kind="study_guide"
    )
    assert [c["source_label"] for c in artifact["citations"]] == ["S1", "S3"]
    assert artifact["citations"][0]["quote"] == first_chunk.text
    assert artifact["citations"][1]["quote"] == "第三段证据片段"

    # 3. Flashcard review idempotency_key deduplication & non-empty sources
    monkeypatch.setattr(
        studio,
        "invoke_chat",
        lambda *_a, **_kw: SimpleNamespace(
            content='{"cards":[{"front":"Q1","back":"A1","source_indices":[2]}]}'
        ),
    )
    deck = studio.generate_flashcard_deck(
        db, owner_user_id=owner, knowledge_base_id=kb_id, count=1
    )
    card_id = UUID(deck["cards"][0]["id"])

    rev1 = studio.review_flashcard(
        db, owner_user_id=owner, card_id=card_id, rating=3, idempotency_key="idem-review-1"
    )
    assert rev1["repetition_count"] == 1
    assert len(rev1["sources"]) == 1

    rev_dup = studio.review_flashcard(
        db, owner_user_id=owner, card_id=card_id, rating=3, idempotency_key="idem-review-1"
    )
    assert rev_dup["repetition_count"] == 1
    assert len(rev_dup["sources"]) == 1

    with pytest.raises(ApiError) as exc_info:
        studio.review_flashcard(
            db, owner_user_id=owner, card_id=card_id, rating=0, idempotency_key="idem-review-1"
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "idempotency_conflict"



def test_append_note_remaps_sparse_sources_and_preserves_verified_snapshot(learning_env):
    from study_platform.api.v1.auth import get_current_user
    from study_platform.persistence.models import (
        Conversation,
        Message,
        MessageCitation,
        NoteCitation,
    )
    from study_platform.services import studio

    http, db, owner, kb_id = learning_env
    user_obj = db.get(User, owner)
    http.app.dependency_overrides[get_current_user] = lambda: user_obj
    try:
        first_chunk = db.scalar(
            sa.select(MaterialChunk)
            .join(MaterialVersion, MaterialVersion.id == MaterialChunk.material_version_id)
            .join(Material, Material.id == MaterialVersion.material_id)
            .where(Material.owner_user_id == owner, Material.knowledge_base_id == kb_id)
        )
        chunks = [first_chunk]
        for ordinal, text in ((1, "追加快照二"), (2, "追加快照三")):
            chunk = MaterialChunk(
                id=uuid4(),
                index_version_id=first_chunk.index_version_id,
                material_version_id=first_chunk.material_version_id,
                ordinal=ordinal,
                text=text,
                text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                locator={"page": ordinal + 1},
            )
            db.add(chunk)
            db.flush()
            chunks.append(chunk)

        note = studio.create_note(
            db,
            owner_user_id=owner,
            knowledge_base_id=kb_id,
            title="已有研究笔记",
            content_md="已有结论 [S4]。",
        )
        db.add(
            NoteCitation(
                note_id=UUID(note["id"]),
                chunk_id=first_chunk.id,
                material_version_id=first_chunk.material_version_id,
                index_version_id=first_chunk.index_version_id,
                locator={"page": 1},
                quote_text=first_chunk.text,
                quote_sha256=hashlib.sha256(first_chunk.text.encode()).hexdigest(),
                ordinal=3,
            )
        )
        conversation = Conversation(id=uuid4(), owner_user_id=owner, title="笔记追加测试")
        db.add(conversation)
        db.flush()
        content = (
            "## 回答依据 [S2]\n结论见 [S2]、[S4]、[S6]，未匹配来源 [S99]。\n"
            "行内代码 `[S4]` 保持原样。\n\n```text\n[S2] [S4] [S6]\n```\n"
        )
        message = Message(
            id=uuid4(),
            conversation_id=conversation.id,
            role="assistant",
            content=content,
            status="completed",
            sequence_no=1,
        )
        db.add(message)
        db.flush()
        for ordinal, chunk in ((1, chunks[0]), (3, chunks[1]), (5, chunks[2])):
            digest = "0" * 64 if ordinal == 5 else hashlib.sha256(chunk.text.encode()).hexdigest()
            db.add(
                MessageCitation(
                    message_id=message.id,
                    chunk_id=chunk.id,
                    material_version_id=chunk.material_version_id,
                    index_version_id=chunk.index_version_id,
                    locator=chunk.locator,
                    quote_sha256=digest,
                    ordinal=ordinal,
                )
            )
        db.flush()

        response = http.post(
            f"/notes/{note['id']}/append",
            json={
                "content_md": content,
                "section_heading": "追加复盘",
                "message_id": str(message.id),
            },
        )
        assert response.status_code == 200
        saved = response.json()
        assert saved["edit_revision"] == 1
        assert "\n\n---\n\n## 追加复盘\n\n" in saved["content_md"]
        assert "结论见 [S6]、[S8]、[S10]" in saved["content_md"]
        assert "行内代码 `[S4]` 保持原样。" in saved["content_md"]
        assert "```text\n[S2] [S4] [S6]\n```" in saved["content_md"]
        assert "未匹配来源 `[S99]`" in saved["content_md"]
        citations = saved["citations"]
        assert [citation["source_label"] for citation in citations] == ["S4", "S6", "S8", "S10"]
        assert citations[1]["quote"] == chunks[0].text
        assert citations[2]["quote"] == chunks[1].text
        assert citations[3]["quote"] == ""
        assert citations[3]["quote_status"] == "unavailable"

        note_citations = db.scalars(
            sa.select(NoteCitation)
            .where(NoteCitation.note_id == UUID(note["id"]))
            .order_by(NoteCitation.ordinal)
        ).all()
        assert [citation.ordinal for citation in note_citations] == [3, 5, 7, 9]
        assert note_citations[1].quote_sha256 == hashlib.sha256(chunks[0].text.encode()).hexdigest()
        assert note_citations[3].quote_sha256 == "0" * 64
        assert note_citations[3].quote_text is None
    finally:
        http.app.dependency_overrides.pop(get_current_user, None)


def test_chat_note_paths_reject_stale_cross_library_unowned_and_oversized_content(learning_env):
    from study_platform.api.v1.auth import get_current_user
    from study_platform.persistence.models import (
        AiWorkflowRun,
        Conversation,
        Course,
        KnowledgeBase,
        Message,
    )
    from study_platform.services import studio

    http, db, owner, kb_id = learning_env
    user_obj = db.get(User, owner)
    http.app.dependency_overrides[get_current_user] = lambda: user_obj
    try:
        note = studio.create_note(
            db,
            owner_user_id=owner,
            knowledge_base_id=kb_id,
            title="目标笔记",
            content_md="已有内容",
        )
        other_kb = KnowledgeBase(id=uuid4(), owner_user_id=owner, name="另一知识库")
        db.add(other_kb)
        db.flush()
        db.add(Course(id=other_kb.id, owner_user_id=owner, name=other_kb.name))
        db.flush()

        def scoped_message(
            *,
            target_kb_id: UUID,
            content: str,
            status: str = "completed",
            role: str = "assistant",
        ):
            conversation = Conversation(id=uuid4(), owner_user_id=owner, title="消息范围测试")
            db.add(conversation)
            db.flush()
            run = AiWorkflowRun(
                id=uuid4(),
                owner_user_id=owner,
                conversation_id=conversation.id,
                knowledge_base_id=target_kb_id,
                answer_mode="knowledge_only",
                status="completed",
                source_scope={"knowledge_base_id": str(target_kb_id)},
            )
            db.add(run)
            db.flush()
            message = Message(
                id=uuid4(),
                conversation_id=conversation.id,
                role=role,
                content=content,
                status=status,
                sequence_no=1,
                workflow_run_id=run.id,
            )
            db.add(message)
            db.flush()
            return message

        cross_library = scoped_message(target_kb_id=other_kb.id, content="另一库答案")
        create_cross = http.post(
            f"/knowledge-bases/{kb_id}/notes",
            json={
                "title": "跨库尝试",
                "content_md": "另一库答案",
                "message_id": str(cross_library.id),
            },
        )
        append_cross = http.post(
            f"/notes/{note['id']}/append",
            json={"content_md": "另一库答案", "message_id": str(cross_library.id)},
        )
        assert create_cross.status_code == append_cross.status_code == 409
        assert create_cross.json()["code"] == append_cross.json()["code"] == "cross_library_scope"

        canonical = scoped_message(target_kb_id=kb_id, content="新的规范答案")
        create_stale = http.post(
            f"/knowledge-bases/{kb_id}/notes",
            json={"title": "过期回答", "content_md": "旧答案", "message_id": str(canonical.id)},
        )
        append_stale = http.post(
            f"/notes/{note['id']}/append",
            json={"content_md": "旧答案", "message_id": str(canonical.id)},
        )
        assert create_stale.status_code == append_stale.status_code == 409
        assert create_stale.json()["code"] == append_stale.json()["code"] == "message_changed"

        incomplete = scoped_message(target_kb_id=kb_id, content="仍在生成", status="streaming")
        incomplete_response = http.post(
            f"/notes/{note['id']}/append",
            json={"content_md": incomplete.content, "message_id": str(incomplete.id)},
        )
        assert incomplete_response.status_code == 409
        assert incomplete_response.json()["code"] == "message_not_completed"

        non_assistant = scoped_message(
            target_kb_id=kb_id, content="用户消息", role="user"
        )
        non_assistant_response = http.post(
            f"/notes/{note['id']}/append",
            json={"content_md": non_assistant.content, "message_id": str(non_assistant.id)},
        )
        assert non_assistant_response.status_code == 409
        assert non_assistant_response.json()["code"] == "message_not_completed"

        missing_message = http.post(
            f"/notes/{note['id']}/append",
            json={"content_md": "正文", "message_id": str(uuid4())},
        )
        assert missing_message.status_code == 404
        assert http.post(f"/notes/{uuid4()}/append", json={"content_md": "正文"}).status_code == 404

        private_user = User(
            id=uuid4(),
            email=f"{uuid4().hex}@example.test",
            password_hash="x",
            display_name="Other",
        )
        db.add(private_user)
        db.flush()
        private_conversation = Conversation(
            id=uuid4(), owner_user_id=private_user.id, title="私有会话"
        )
        db.add(private_conversation)
        db.flush()
        private_message = Message(
            id=uuid4(),
            conversation_id=private_conversation.id,
            role="assistant",
            content="私人回答",
            status="completed",
            sequence_no=1,
        )
        db.add(private_message)
        db.flush()
        private_kb = KnowledgeBase(id=uuid4(), owner_user_id=private_user.id, name="私有知识库")
        db.add(private_kb)
        db.flush()
        db.add(Course(id=private_kb.id, owner_user_id=private_user.id, name=private_kb.name))
        db.flush()
        private_note = studio.create_note(
            db,
            owner_user_id=private_user.id,
            knowledge_base_id=private_kb.id,
            title="私有笔记",
            content_md="私人内容",
        )
        private_append = http.post(
            f"/notes/{private_note['id']}/append", json={"content_md": "越权追加"}
        )
        assert private_append.status_code == 404
        private_message_append = http.post(
            f"/notes/{note['id']}/append",
            json={"content_md": "私人回答", "message_id": str(private_message.id)},
        )
        assert private_message_append.status_code == 404

        long_note = studio.create_note(
            db,
            owner_user_id=owner,
            knowledge_base_id=kb_id,
            title="长笔记",
            content_md="x" * 199995,
        )
        oversized = http.post(
            f"/notes/{long_note['id']}/append", json={"content_md": "再追加一点"}
        )
        assert oversized.status_code == 422
        assert oversized.json()["code"] == "note_too_long"
    finally:
        http.app.dependency_overrides.pop(get_current_user, None)


def test_vision_grading_is_opt_in_and_safely_pending_on_low_confidence_or_failure(learning_env, monkeypatch):
    from study_platform.api.v1.auth import get_current_user

    http, db, owner, kb_id = learning_env
    http.app.dependency_overrides[get_current_user] = lambda: db.get(User, owner)
    quiz = learning.generate_learning_quiz(db, owner_user_id=owner, knowledge_base_id=kb_id, count=1)
    attempt = learning.start_attempt(db, owner_user_id=owner, quiz_id=quiz.id)
    response_id = learning.attempt_payload(db, attempt.id, owner)["items"][0]["response_id"]
    route = f"/learning/responses/{response_id}/answer-image"
    image = {"file": ("answer.png", _png_with_text("x"), "image/png")}
    calls = []
    snapshot = {"id": str(uuid4()), "kind": "vision", "revision": 1}

    def interpret(*_args, context, saved):
        assert saved == snapshot and context.purpose == "vision_grade"
        calls.append(saved)
        if len(calls) > 1:
            raise ApiError(504, "model_timeout", "fake timeout")
        return '{"transcribed_text":"x", "verdict":"correct", "confidence":0.4, "feedback":"检查结果"}'

    monkeypatch.setattr(learning, "ocr_answer_bytes", lambda _: ("local OCR", 0.9))
    monkeypatch.setattr(model_calls, "invoke_vision", interpret)
    try:
        regular = http.post(route, files=image)
        assert regular.status_code == 200 and regular.json()["answer"]["ocr_text"] == "local OCR"
        assert not calls

        no_model = http.post(route, files=image, data={"use_vision": "true"})
        assert no_model.status_code == 409 and no_model.json()["code"] == "vision_not_configured"
        monkeypatch.setattr(model_connections, "vision_snapshot", lambda *_: snapshot)
        monkeypatch.setattr(model_connections, "task", lambda *_, **_kwargs: nullcontext())
        opted_in = http.post(route, files=image, data={"use_vision": "true"})
        assert opted_in.status_code == 200
        data = opted_in.json()
        assert data["verdict"] == "pending" and data["confidence"] == 0.4
        assert data["answer"]["extraction"] == "vlm" and data["answer"]["transcribed_text"] == "x"
        assert "ocr_text" not in data["answer"]

        timed_out = http.post(route, files=image, data={"use_vision": "true"})
        assert timed_out.status_code == 200 and timed_out.json()["verdict"] == "pending"
        assert timed_out.json()["answer"]["extraction"] == "vlm_failed"
        manual = http.patch(f"/learning/responses/{response_id}/verdict", json={"verdict": "incorrect"})
        assert manual.status_code == 200 and manual.json()["verdict"] == "incorrect"
        assert len(http.get("/learning/wrong-questions").json()["items"]) == 1
    finally:
        http.app.dependency_overrides.pop(get_current_user, None)


def test_learning_regressions_mcq_image_override_cleanup_deleted_kb_and_retry_feedback(
    learning_env, monkeypatch
):
    from pathlib import Path

    from study_platform.api.v1.auth import get_current_user

    http, db, owner, kb_id = learning_env
    user_obj = db.get(User, owner)
    http.app.dependency_overrides[get_current_user] = lambda: user_obj
    try:
        quiz = learning.generate_learning_quiz(
            db, owner_user_id=owner, knowledge_base_id=kb_id, count=1, mode="mcq"
        )
        attempt = learning.start_attempt(db, owner_user_id=owner, quiz_id=quiz.id)
        payload = learning.attempt_payload(db, attempt.id, owner)
        resp_id = payload["items"][0]["response_id"]

        # 1. Item 7a: Manual verdict override on unanswered question populates reference_answer
        patch_res = http.patch(
            f"/learning/responses/{resp_id}/verdict", json={"verdict": "incorrect"}
        )
        assert patch_res.status_code == 200
        patched_data = patch_res.json()
        assert patched_data["reference_answer"]

        # 2. Item 2c: Handwritten MCQ image with single letter grades correct
        expected_letter = patched_data["reference_answer"][0]
        monkeypatch.setattr(
            learning, "ocr_answer_bytes", lambda _bytes: (expected_letter, 0.96)
        )
        img_res = http.post(
            f"/learning/responses/{resp_id}/answer-image",
            files={"file": ("ans.png", _png_with_text(expected_letter), "image/png")},
        )
        assert img_res.status_code == 200
        img_data = img_res.json()
        assert img_data["verdict"] == "correct"
        saved_key = img_data["answer"]["storage_key"]
        saved_file = Path(learning.get_settings().storage_dir).resolve() / saved_key
        assert saved_file.is_file()

        # 3. Item 7b: Switching to text answer deletes the previous handwritten image file
        wrong_letter = "D" if expected_letter != "D" else "A"
        txt_res = http.post(
            f"/learning/responses/{resp_id}/answer-text",
            json={"answer_text": wrong_letter},
        )
        assert txt_res.status_code == 200
        assert txt_res.json()["verdict"] == "incorrect"
        assert not saved_file.exists()

        # 4. Item 9: Retrying wrong question and answering wrong again updates last_answer & last_feedback
        wrongs1 = http.get("/learning/wrong-questions").json()["items"]
        assert len(wrongs1) == 1
        wrong_id = wrongs1[0]["id"]
        assert wrongs1[0]["last_answer"] == wrong_letter

        retry_res = http.post(
            "/learning/wrong-questions/retry", json={"wrong_ids": [wrong_id]}
        )
        assert retry_res.status_code == 200
        retry_resp_id = retry_res.json()["items"][0]["response_id"]

        second_wrong = "C" if expected_letter != "C" else "B"
        txt_res2 = http.post(
            f"/learning/responses/{retry_resp_id}/answer-text",
            json={"answer_text": f"{second_wrong}. 重练第二次选错"},
        )
        assert txt_res2.status_code == 200
        assert txt_res2.json()["verdict"] == "incorrect"

        wrongs2 = http.get("/learning/wrong-questions").json()["items"]
        assert wrongs2[0]["wrong_count"] >= 2
        assert wrongs2[0]["last_answer"] == f"{second_wrong}. 重练第二次选错"
        assert wrongs2[0]["is_due"] is True
        assert datetime.fromisoformat(wrongs2[0]["next_review_at"]) <= datetime.now(UTC)

        # 5. Item 8a: Soft-deleting KB still allows retrying wrong question even when knowledge_base_id is passed
        kb = db.get(KnowledgeBase, kb_id)
        kb.deleted_at = datetime.now(UTC)
        db.commit()

        retry_deleted_kb = http.post(
            "/learning/wrong-questions/retry",
            json={"wrong_ids": [wrong_id], "knowledge_base_id": str(kb_id)},
        )
        assert retry_deleted_kb.status_code == 200
        assert len(retry_deleted_kb.json()["items"]) == 1
    finally:
        http.app.dependency_overrides.pop(get_current_user, None)


def test_wrong_review_schedule_due_filters_retry_and_dashboard(learning_env):
    from study_platform.api.v1.auth import get_current_user
    from study_platform.persistence.models import WrongQuestion

    http, db, owner, kb_id = learning_env
    user_obj = db.get(User, owner)
    http.app.dependency_overrides[get_current_user] = lambda: user_obj
    try:
        quiz = learning.generate_learning_quiz(
            db, owner_user_id=owner, knowledge_base_id=kb_id, count=1, mode="mcq"
        )
        initial_attempt = learning.start_attempt(db, owner_user_id=owner, quiz_id=quiz.id)
        response_id = learning.attempt_payload(db, initial_attempt.id, owner)["items"][0]["response_id"]

        incorrect = http.patch(
            f"/learning/responses/{response_id}/verdict", json={"verdict": "incorrect"}
        )
        assert incorrect.status_code == 200
        wrong = db.scalar(sa.select(WrongQuestion).where(WrongQuestion.owner_user_id == owner))
        assert wrong is not None
        wrong_id = wrong.id

        due_rows = http.get("/learning/wrong-questions", params={"due_only": "true"}).json()["items"]
        assert [row["id"] for row in due_rows] == [str(wrong_id)]
        assert due_rows[0]["next_review_at"] is not None
        assert due_rows[0]["is_due"] is True
        overview = http.get("/dashboard/overview")
        assert overview.status_code == 200
        assert overview.json()["due_wrong_question_count"] == 1

        first_retry = http.post("/learning/wrong-questions/retry", json={"due_only": True})
        assert first_retry.status_code == 200
        retry_response_id = first_retry.json()["items"][0]["response_id"]
        before_three_days = datetime.now(UTC)
        corrected = http.patch(
            f"/learning/responses/{retry_response_id}/verdict", json={"verdict": "correct"}
        )
        assert corrected.status_code == 200
        first_schedule = datetime.fromisoformat(
            http.get("/learning/wrong-questions").json()["items"][0]["next_review_at"]
        )
        assert before_three_days + timedelta(days=3) <= first_schedule
        assert first_schedule <= datetime.now(UTC) + timedelta(days=3, seconds=2)
        assert http.get("/learning/wrong-questions", params={"due_only": "true"}).json()["items"] == []
        assert http.post("/learning/wrong-questions/retry", json={"due_only": True}).status_code == 409

        # Explicit wrong IDs keep working even when the user requests a due-only queue.
        second_retry = http.post(
            "/learning/wrong-questions/retry",
            json={"wrong_ids": [str(wrong_id)], "due_only": True},
        )
        assert second_retry.status_code == 200
        retry_response_id = second_retry.json()["items"][0]["response_id"]
        before_seven_days = datetime.now(UTC)
        corrected = http.patch(
            f"/learning/responses/{retry_response_id}/verdict", json={"verdict": "correct"}
        )
        assert corrected.status_code == 200
        second_schedule = datetime.fromisoformat(
            http.get("/learning/wrong-questions").json()["items"][0]["next_review_at"]
        )
        assert before_seven_days + timedelta(days=7) <= second_schedule
        assert second_schedule <= datetime.now(UTC) + timedelta(days=7, seconds=2)

        # Legacy reviewing rows keep NULL storage and use last_wrong_at + 3d for due checks.
        wrong.status = "reviewing"
        wrong.last_wrong_at = datetime.now(UTC) - timedelta(days=2)
        wrong.next_review_at = None
        db.flush()
        legacy = http.get("/learning/wrong-questions").json()["items"][0]
        assert legacy["next_review_at"] is None
        assert legacy["is_due"] is False
        wrong.last_wrong_at = datetime.now(UTC) - timedelta(days=4)
        db.flush()
        legacy_due = http.get(
            "/learning/wrong-questions", params={"due_only": "true"}
        ).json()["items"]
        assert legacy_due[0]["is_due"] is True
        overview = http.get("/dashboard/overview")
        assert overview.json()["due_wrong_question_count"] == 1

        legacy_retry = http.post("/learning/wrong-questions/retry", json={"due_only": True})
        assert legacy_retry.status_code == 200
        retry_response_id = legacy_retry.json()["items"][0]["response_id"]
        before_legacy_schedule = datetime.now(UTC)
        corrected = http.patch(
            f"/learning/responses/{retry_response_id}/verdict", json={"verdict": "correct"}
        )
        assert corrected.status_code == 200
        recovered_schedule = datetime.fromisoformat(
            http.get("/learning/wrong-questions").json()["items"][0]["next_review_at"]
        )
        assert before_legacy_schedule + timedelta(days=3) <= recovered_schedule
        assert recovered_schedule <= datetime.now(UTC) + timedelta(days=3, seconds=2)

        mastered = http.post(f"/learning/wrong-questions/{wrong_id}/mastered")
        assert mastered.status_code == 200
        db.refresh(wrong)
        assert wrong.status == "mastered"
        assert wrong.next_review_at is None
        assert http.get("/dashboard/overview").json()["due_wrong_question_count"] == 0
    finally:
        http.app.dependency_overrides.pop(get_current_user, None)


def test_learning_attempt_history_cursor_pagination_and_scope(learning_env):
    from study_platform.api.v1.auth import get_current_user
    from study_platform.persistence.models import Quiz, QuizItem

    http, db, owner, kb_id = learning_env
    user_obj = db.get(User, owner)
    http.app.dependency_overrides[get_current_user] = lambda: user_obj
    try:
        quiz = learning.generate_learning_quiz(
            db, owner_user_id=owner, knowledge_base_id=kb_id, count=1, mode="short_answer"
        )
        created_ids = [
            str(learning.start_attempt(db, owner_user_id=owner, quiz_id=quiz.id).id)
            for _ in range(21)
        ]

        first = http.get("/learning/attempts", params={"knowledge_base_id": str(kb_id)})
        assert first.status_code == 200
        first_page = first.json()
        assert len(first_page["items"]) == 20
        assert first_page["next_cursor"]
        second = http.get(
            "/learning/attempts",
            params={"knowledge_base_id": str(kb_id), "cursor": first_page["next_cursor"]},
        )
        assert second.status_code == 200
        second_page = second.json()
        assert len(second_page["items"]) == 1
        assert second_page["next_cursor"] is None
        listed_ids = [row["id"] for row in first_page["items"] + second_page["items"]]
        assert len(set(listed_ids)) == len(created_ids)
        assert set(listed_ids) == set(created_ids)
        assert isinstance(learning.list_attempts(db, owner, knowledge_base_id=kb_id), list)

        invalid = http.get("/learning/attempts", params={"cursor": str(uuid4())})
        assert invalid.status_code == 400
        assert invalid.json()["code"] == "invalid_cursor"

        other_kb = KnowledgeBase(id=uuid4(), owner_user_id=owner, name="另一练习库")
        db.add(other_kb)
        db.flush()
        db.add(Course(id=other_kb.id, owner_user_id=owner, name=other_kb.name))
        db.flush()
        other_quiz = Quiz(
            owner_user_id=owner,
            course_id=other_kb.id,
            title="另一库练习",
            status="ready",
            difficulty=3,
            source_scope={},
        )
        db.add(other_quiz)
        db.flush()
        db.add(
            QuizItem(
                quiz_id=other_quiz.id,
                ordinal=0,
                item_type="short_answer",
                stem="另一库题目",
                options=[],
                answer_key={"text": "参考答案"},
                rubric={},
                difficulty=3,
                max_score=1,
            )
        )
        db.flush()
        other_attempt = learning.start_attempt(db, owner_user_id=owner, quiz_id=other_quiz.id)
        scoped_cursor = http.get(
            "/learning/attempts",
            params={"knowledge_base_id": str(kb_id), "cursor": str(other_attempt.id)},
        )
        assert scoped_cursor.status_code == 400
        assert scoped_cursor.json()["code"] == "invalid_cursor"
    finally:
        http.app.dependency_overrides.pop(get_current_user, None)


def test_local_mcq_ignores_unready_model_and_quote_snapshot(learning_env, monkeypatch):
    from contextlib import contextmanager

    from study_platform.core.errors import ApiError
    from study_platform.persistence.models import QuizItemSource

    _, db, owner, kb_id = learning_env
    quiz = learning.generate_learning_quiz(db, owner_user_id=owner, knowledge_base_id=kb_id, count=1, mode="mcq")
    attempt = learning.start_attempt(db, owner_user_id=owner, quiz_id=quiz.id)
    item = learning.attempt_payload(db, attempt.id, owner)["items"][0]
    assert item["sources"][0]["quote"] is None
    assert item["sources"][0]["quote_status"] is None
    source = db.scalar(sa.select(QuizItemSource).where(QuizItemSource.quiz_item_id == UUID(item["quiz_item_id"])))
    original = source.quote_text
    chunk = db.get(MaterialChunk, source.chunk_id)
    chunk.text = "资料后来被修改"
    db.flush()

    @contextmanager
    def pending(*_args, **_kwargs):
        raise ApiError(409, "model_test_required", "连接待测试")
        yield
    monkeypatch.setattr(learning.model_connections, "task", pending)
    row = learning.submit_text_answer(db, owner_user_id=owner, response_id=UUID(item["response_id"]), answer_text="A")
    assert row.verdict == "correct"
    monkeypatch.setattr(learning, "ocr_answer_bytes", lambda _bytes: ("A", 0.99))
    image_row = learning.submit_image_answer(
        db, owner_user_id=owner, response_id=row.id, image_bytes=_png_with_text("A")
    )
    assert image_row.verdict == "correct"
    quiz_item = db.get(learning.QuizItem, UUID(item["quiz_item_id"]))
    quiz_item.item_type = "short_answer"
    db.flush()
    with pytest.raises(ApiError) as error:
        learning.submit_text_answer(db, owner_user_id=owner, response_id=row.id, answer_text="other")
    assert error.value.code == "model_test_required"
    source_payload = learning.attempt_payload(db, attempt.id, owner)["items"][0]["sources"][0]
    assert source_payload["quote"] == original
    assert source_payload["quote_status"] == "verified"
    source.quote_text = None
    db.flush()
    source_payload = learning.attempt_payload(db, attempt.id, owner)["items"][0]["sources"][0]
    assert source_payload["quote"] == "资料后来被修改"
    assert source_payload["quote_status"] == "unverified"
    db.get(KnowledgeBase, kb_id).deleted_at = datetime.now(UTC)
    db.flush()
    source_payload = learning.attempt_payload(db, attempt.id, owner)["items"][0]["sources"][0]
    assert source_payload["quote"] is None
    assert source_payload["quote_status"] == "unavailable"


def test_wrong_retry_uses_each_question_library_and_copies_quote(learning_env):
    from study_platform.persistence.models import Quiz, QuizItem, QuizItemSource, WrongQuestion

    _, db, owner, kb_id = learning_env
    first = learning.generate_learning_quiz(db, owner_user_id=owner, knowledge_base_id=kb_id, count=1, mode="mcq")
    original = db.scalar(sa.select(QuizItem).where(QuizItem.quiz_id == first.id))
    source = db.scalar(sa.select(QuizItemSource).where(QuizItemSource.quiz_item_id == original.id))
    other_kb = KnowledgeBase(id=uuid4(), owner_user_id=owner, name="另一知识库")
    db.add(other_kb)
    db.flush()
    db.add(Course(id=other_kb.id, owner_user_id=owner, name=other_kb.name))
    second = Quiz(owner_user_id=owner, course_id=other_kb.id, title="另一练习", status="ready", difficulty=3, source_scope={})
    db.add(second)
    db.flush()
    second_item = QuizItem(quiz_id=second.id, ordinal=0, item_type="mcq", stem="题目", options=["A. 一", "B. 二"], answer_key={"choice": "A"}, rubric={}, difficulty=3, max_score=1)
    db.add(second_item)
    db.flush()
    for quiz in (first, second):
        attempt = learning.start_attempt(db, owner_user_id=owner, quiz_id=quiz.id)
        response = learning.attempt_payload(db, attempt.id, owner)["items"][0]
        learning.submit_text_answer(db, owner_user_id=owner, response_id=UUID(response["response_id"]), answer_text="B")
    wrongs = db.scalars(sa.select(WrongQuestion).where(WrongQuestion.owner_user_id == owner)).all()
    retry = learning.retry_wrong_questions(db, owner_user_id=owner, wrong_ids=[w.id for w in wrongs])
    items = learning.attempt_payload(db, retry.id, owner)["items"]
    assert {item["knowledge_base_id"] for item in items} == {str(kb_id), str(other_kb.id)}
    cloned = next(item for item in items if item["knowledge_base_id"] == str(kb_id))
    copied_source = db.scalar(sa.select(QuizItemSource).where(QuizItemSource.quiz_item_id == UUID(cloned["quiz_item_id"])))
    assert copied_source.quote_text == source.quote_text

    retry_item = db.get(QuizItem, UUID(cloned["quiz_item_id"]))
    retry_item.rubric = {**retry_item.rubric, "origin_wrong_id": str(uuid4())}
    other_kb.deleted_at = datetime.now(UTC)
    db.flush()
    unavailable = learning.attempt_payload(db, retry.id, owner)["items"]
    assert all(item["knowledge_base_id"] is None for item in unavailable)


def test_concurrent_answer_updates_serialize_attempt_progress():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from study_platform.persistence.models import (
        Quiz,
        QuizAttempt,
        QuizItem,
        QuizResponse,
        WrongQuestion,
    )

    if os.getenv("RUN_DB_TESTS") != "1" or not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("explicit PostgreSQL test database required")
    url = make_url(os.environ["TEST_DATABASE_URL"])
    if not (url.database or "").endswith("_test"):
        pytest.fail("isolated *_test database required")
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    owner, kb_id = uuid4(), uuid4()
    first_done, release_first, second_done = Event(), Event(), Event()
    try:
        with Session(engine) as db:
            db.add(User(id=owner, email=f"{owner.hex}@test.example", password_hash="x", display_name="Test"))
            db.add(KnowledgeBase(id=kb_id, owner_user_id=owner, name="Test"))
            db.flush()
            db.add(Course(id=kb_id, owner_user_id=owner, name="Test"))
            quiz = Quiz(owner_user_id=owner, course_id=kb_id, title="Test", status="ready", difficulty=3, source_scope={})
            db.add(quiz)
            db.flush()
            for ordinal in range(2):
                db.add(QuizItem(quiz_id=quiz.id, ordinal=ordinal, item_type="mcq", stem="题", options=["A. 一", "B. 二"], answer_key={"choice": "A"}, rubric={}, difficulty=3, max_score=1))
            db.flush()
            attempt = learning.start_attempt(db, owner_user_id=owner, quiz_id=quiz.id)
            attempt_id = attempt.id
            ids = [UUID(item["response_id"]) for item in learning.attempt_payload(db, attempt.id, owner)["items"]]
            db.commit()

        def first():
            with Session(engine) as db:
                learning.submit_text_answer(db, owner_user_id=owner, response_id=ids[0], answer_text="A")
                first_done.set()
                assert release_first.wait(5)
                db.commit()

        def second():
            with Session(engine) as db:
                learning.override_verdict(db, owner_user_id=owner, response_id=ids[1], verdict="correct", reference_answer=None)
                db.commit()
                second_done.set()

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(first)
            assert first_done.wait(5)
            b = pool.submit(second)
            try:
                assert not second_done.wait(0.2)
            finally:
                release_first.set()
            a.result(timeout=5)
            b.result(timeout=5)
        with Session(engine) as db:
            attempt = db.get(QuizAttempt, attempt_id)
            assert attempt.status == "graded"
            assert attempt.total_score == 2
    finally:
        release_first.set()
        with Session(engine) as db:
            quiz_ids = sa.select(Quiz.id).where(Quiz.owner_user_id == owner)
            attempt_ids = sa.select(QuizAttempt.id).where(QuizAttempt.owner_user_id == owner)
            db.execute(sa.delete(WrongQuestion).where(WrongQuestion.owner_user_id == owner))
            db.execute(sa.delete(QuizResponse).where(QuizResponse.attempt_id.in_(attempt_ids)))
            db.execute(sa.delete(QuizAttempt).where(QuizAttempt.owner_user_id == owner))
            db.execute(sa.delete(QuizItem).where(QuizItem.quiz_id.in_(quiz_ids)))
            db.execute(sa.delete(Quiz).where(Quiz.owner_user_id == owner))
            db.execute(sa.delete(Course).where(Course.id == kb_id))
            db.execute(sa.delete(KnowledgeBase).where(KnowledgeBase.id == kb_id))
            db.execute(sa.delete(User).where(User.id == owner))
            db.commit()
        engine.dispose()


def test_concurrent_wrong_answers_create_one_review_record():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from study_platform.persistence.models import (
        Quiz,
        QuizAttempt,
        QuizItem,
        QuizResponse,
        WrongQuestion,
    )

    if os.getenv("RUN_DB_TESTS") != "1" or not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("explicit PostgreSQL test database required")
    url = make_url(os.environ["TEST_DATABASE_URL"])
    if not (url.database or "").endswith("_test"):
        pytest.fail("isolated *_test database required")
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    owner, kb_id = uuid4(), uuid4()
    barrier = Barrier(2)
    try:
        with Session(engine) as db:
            db.add(User(id=owner, email=f"{owner.hex}@test.example", password_hash="x", display_name="Test"))
            db.add(KnowledgeBase(id=kb_id, owner_user_id=owner, name="Test"))
            db.flush()
            db.add(Course(id=kb_id, owner_user_id=owner, name="Test"))
            quiz = Quiz(owner_user_id=owner, course_id=kb_id, title="Test", status="ready", difficulty=3, source_scope={})
            db.add(quiz)
            db.flush()
            item = QuizItem(quiz_id=quiz.id, ordinal=0, item_type="mcq", stem="题", options=["A. 一", "B. 二"], answer_key={"choice": "A"}, rubric={}, difficulty=3, max_score=1)
            db.add(item)
            db.flush()
            responses = []
            for _ in range(2):
                attempt = learning.start_attempt(db, owner_user_id=owner, quiz_id=quiz.id)
                responses.append(learning.attempt_payload(db, attempt.id, owner)["items"][0]["response_id"])
            response_ids = [UUID(value) for value in responses]
            db.commit()

        def mark_incorrect(response_id: UUID) -> None:
            with Session(engine) as db:
                barrier.wait(timeout=5)
                learning.override_verdict(
                    db,
                    owner_user_id=owner,
                    response_id=response_id,
                    verdict="incorrect",
                    reference_answer=None,
                )
                db.commit()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(mark_incorrect, response_id) for response_id in response_ids]
            for future in futures:
                future.result(timeout=10)

        with Session(engine) as db:
            wrongs = db.scalars(sa.select(WrongQuestion).where(WrongQuestion.owner_user_id == owner)).all()
            assert len(wrongs) == 1
            assert wrongs[0].wrong_count == 2
            assert wrongs[0].next_review_at is not None
    finally:
        with Session(engine) as db:
            quiz_ids = sa.select(Quiz.id).where(Quiz.owner_user_id == owner)
            attempt_ids = sa.select(QuizAttempt.id).where(QuizAttempt.owner_user_id == owner)
            db.execute(sa.delete(WrongQuestion).where(WrongQuestion.owner_user_id == owner))
            db.execute(sa.delete(QuizResponse).where(QuizResponse.attempt_id.in_(attempt_ids)))
            db.execute(sa.delete(QuizAttempt).where(QuizAttempt.owner_user_id == owner))
            db.execute(sa.delete(QuizItem).where(QuizItem.quiz_id.in_(quiz_ids)))
            db.execute(sa.delete(Quiz).where(Quiz.owner_user_id == owner))
            db.execute(sa.delete(Course).where(Course.id == kb_id))
            db.execute(sa.delete(KnowledgeBase).where(KnowledgeBase.id == kb_id))
            db.execute(sa.delete(User).where(User.id == owner))
            db.commit()
        engine.dispose()
