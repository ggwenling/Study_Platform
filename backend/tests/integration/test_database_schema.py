"""PostgreSQL contract tests; never migrate or commit data."""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError

from study_platform.persistence import models as m
from study_platform.persistence.base import Base

pytestmark = pytest.mark.integration


@pytest.fixture
def db():
    if os.getenv("RUN_DB_TESTS") != "1":
        pytest.skip("set RUN_DB_TESTS=1 to run PostgreSQL tests")
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    parsed = make_url(url)
    if not parsed.database or not parsed.database.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL database name must end in _test")
    if parsed.get_backend_name() != "postgresql":
        pytest.fail("TEST_DATABASE_URL must point to PostgreSQL")
    engine = sa.create_engine(
        parsed.set(drivername="postgresql+psycopg"), connect_args={"connect_timeout": 5}
    )
    try:
        with engine.connect() as connection:
            if not connection.scalar(sa.text("SELECT current_database()")).endswith("_test"):
                pytest.fail("connected database name must end in _test")
            transaction = connection.begin_nested()
            try:
                yield connection
            finally:
                transaction.rollback()
                connection.rollback()
    finally:
        engine.dispose()


def insert(db, model, **values):
    return db.execute(
        model.__table__.insert().values(**values).returning(model.__table__.c.id)
    ).scalar_one()


def user_and_course(db):
    owner = insert(
        db, m.User, email=f"{uuid4()}@example.test", password_hash="hash", display_name="Test"
    )
    course = insert(db, m.Course, owner_user_id=owner, name="Course")
    insert(db, m.KnowledgeBase, id=course, owner_user_id=owner, name="Course")
    return owner, course


def material_and_version(db):
    owner, course = user_and_course(db)
    material = insert(
        db,
        m.Material,
        owner_user_id=owner,
        course_id=course,
        knowledge_base_id=course,
        title="M",
        kind="notes",
    )
    version = insert(
        db,
        m.MaterialVersion,
        material_id=material,
        version_no=1,
        is_current=True,
        source_mime="text/plain",
        source_sha256="a" * 64,
        byte_size=1,
        object_key=str(uuid4()),
    )
    return material, version


def test_schema_matches_metadata(db):
    inspector = sa.inspect(db)
    expected = {
        table.name: table for table in Base.metadata.tables.values() if table.schema == "study"
    }
    assert len(expected) == 40
    assert set(inspector.get_table_names(schema="study")) - {"alembic_version"} == set(expected)
    for name, table in expected.items():
        actual = {column["name"]: column for column in inspector.get_columns(name, schema="study")}
        assert set(actual) == set(table.columns.keys())
        for column in table.columns:
            assert actual[column.name]["nullable"] == column.nullable, (name, column.name)
            assert (
                actual[column.name]["type"].compile(dialect=db.dialect).upper()
                == column.type.compile(dialect=db.dialect).upper()
            ), (name, column.name)
        actual_fks = {
            (
                tuple(fk["constrained_columns"]),
                fk["referred_schema"],
                fk["referred_table"],
                tuple(fk["referred_columns"]),
                fk["options"].get("ondelete", "NO ACTION").upper(),
            )
            for fk in inspector.get_foreign_keys(name, schema="study")
        }
        expected_fks = {
            (
                tuple(element.parent.name for element in fk.elements),
                fk.referred_table.schema,
                fk.referred_table.name,
                tuple(element.column.name for element in fk.elements),
                (fk.ondelete or "NO ACTION").upper(),
            )
            for fk in table.foreign_key_constraints
        }
        assert actual_fks == expected_fks, name
    assert db.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"))
    indexes = inspector.get_indexes("chunk_embeddings", schema="study")
    assert any(
        index["name"] == "ix_chunk_embeddings_cosine_hnsw"
        and index["dialect_options"].get("postgresql_using") == "hnsw"
        and index["dialect_options"].get("postgresql_ops", {}).get("embedding")
        == "vector_cosine_ops"
        for index in indexes
    )

    def include_name(name, type_, parent_names):
        if type_ == "schema":
            return name == "study"
        if type_ == "table" and name == "alembic_version":
            return False
        return True

    context = MigrationContext.configure(
        db, opts={"include_schemas": True, "include_name": include_name, "compare_type": True}
    )
    assert compare_metadata(context, Base.metadata) == []


def test_users_citext_and_pending(db):
    email = f"{uuid4()}@example.test"
    user = insert(
        db,
        m.User,
        email=email,
        password_hash="hash",
        display_name="Test",
        status="pending_verification",
    )
    assert (
        db.execute(sa.select(m.User.status).where(m.User.id == user)).scalar_one()
        == "pending_verification"
    )
    with pytest.raises(IntegrityError) as error, db.begin_nested():
        insert(db, m.User, email=email.upper(), password_hash="hash", display_name="Test")
    assert error.value.orig.diag.constraint_name == "uq_users_email"


def test_material_rejects_other_course_owner(db):
    _, course = user_and_course(db)
    other, other_kb = user_and_course(db)
    with pytest.raises(IntegrityError) as error, db.begin_nested():
        insert(
            db,
            m.Material,
            owner_user_id=other,
            course_id=course,
            knowledge_base_id=other_kb,
            title="M",
            kind="notes",
        )
    assert error.value.orig.diag.constraint_name == "fk_materials_course_id_owner_user_id"


def test_current_version_and_active_index_constraints(db):
    material, version = material_and_version(db)
    with pytest.raises(IntegrityError) as error, db.begin_nested():
        insert(
            db,
            m.MaterialVersion,
            material_id=material,
            version_no=2,
            is_current=True,
            source_mime="text/plain",
            source_sha256="b" * 64,
            byte_size=1,
            object_key=str(uuid4()),
        )
    assert error.value.orig.diag.constraint_name == "ix_material_versions_current"
    with pytest.raises(IntegrityError) as error, db.begin_nested():
        insert(
            db,
            m.IndexVersion,
            material_version_id=version,
            generation=1,
            pipeline_profile="test",
            embedding_profile="test",
            status="building",
            is_active=True,
        )
    assert error.value.orig.diag.constraint_name == "ck_index_versions_active_ready"


def test_quiz_response_rejects_item_from_another_quiz(db):
    owner, course = user_and_course(db)
    quiz_a = insert(
        db,
        m.Quiz,
        owner_user_id=owner,
        course_id=course,
        title="A",
        status="draft",
        source_scope={},
    )
    quiz_b = insert(
        db,
        m.Quiz,
        owner_user_id=owner,
        course_id=course,
        title="B",
        status="draft",
        source_scope={},
    )
    item = insert(
        db,
        m.QuizItem,
        quiz_id=quiz_b,
        ordinal=0,
        item_type="short_answer",
        stem="?",
        answer_key="answer",
    )
    attempt = insert(
        db,
        m.QuizAttempt,
        quiz_id=quiz_a,
        owner_user_id=owner,
        status="in_progress",
        started_at=datetime.now(UTC),
        max_score=1,
    )
    with pytest.raises(IntegrityError) as error, db.begin_nested():
        insert(
            db,
            m.QuizResponse,
            attempt_id=attempt,
            quiz_id=quiz_a,
            quiz_item_id=item,
            answer="answer",
        )
    assert error.value.orig.diag.constraint_name == "fk_quiz_responses_quiz_item_id_quiz_id"


def test_vector_dimension_and_cosine_query(db):
    _, version = material_and_version(db)
    index = insert(
        db,
        m.IndexVersion,
        material_version_id=version,
        generation=1,
        pipeline_profile="test",
        embedding_profile="test",
        status="building",
    )
    chunk = insert(
        db,
        m.MaterialChunk,
        material_version_id=version,
        index_version_id=index,
        ordinal=0,
        text="test",
        text_sha256="c" * 64,
        locator={},
    )
    vector = [1.0] + [0.0] * 1023
    db.execute(
        m.ChunkEmbedding.__table__.insert().values(
            chunk_id=chunk, model_profile_id="test", embedding=vector
        )
    )
    distance = db.execute(
        sa.select(m.ChunkEmbedding.embedding.cosine_distance(vector)).where(
            m.ChunkEmbedding.chunk_id == chunk
        )
    ).scalar_one()
    assert distance == pytest.approx(0.0)
    with pytest.raises(DBAPIError, match="expected 1024 dimensions, not 2"), db.begin_nested():
        db.execute(
            sa.text(
                "INSERT INTO study.chunk_embeddings (chunk_id, model_profile_id, embedding) VALUES (:chunk, 'wrong', '[1,2]'::vector)"
            ),
            {"chunk": chunk},
        )
