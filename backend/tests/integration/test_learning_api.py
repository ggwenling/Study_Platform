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