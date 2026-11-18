"""Learning flow: service integration + authenticated wrong-book API."""

import hashlib
import io
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from study_platform.core.config import Settings
from study_platform.core.config import get_settings as load_settings
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
from study_platform.services import chat, learning

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
