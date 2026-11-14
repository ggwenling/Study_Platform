"""Exercise Alembic only in a disposable database created by this test."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

from alembic import command
from study_platform.persistence import models  # noqa: F401
from study_platform.persistence.base import Base

pytestmark = pytest.mark.integration


def test_migration_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.getenv("RUN_DB_TESTS") != "1":
        pytest.skip("set RUN_DB_TESTS=1 to run PostgreSQL tests")
    raw_url = os.getenv("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql" or not (url.database or "").endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must name a PostgreSQL database ending in _test")
    if {"dbname", "service", "servicefile"} & set(url.query):
        pytest.fail("TEST_DATABASE_URL must not override the database name")

    name = f"study_migration_{uuid4().hex}_test"
    url = url.set(drivername="postgresql+psycopg")
    admin_engine = sa.create_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 5},
    )
    created = False
    try:
        with admin_engine.connect() as admin:
            if admin.scalar(sa.text("SELECT current_database()")) != "postgres":
                pytest.fail("administrative connection did not select postgres")
            can_create = admin.scalar(
                sa.text("SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname = current_user")
            )
            if not can_create:
                pytest.skip("database role lacks CREATEDB")
            quoted_name = admin.dialect.identifier_preparer.quote(name)
            admin.exec_driver_sql(f"CREATE DATABASE {quoted_name}")
            created = True

        target = url.set(database=name)
        engine = sa.create_engine(target, connect_args={"connect_timeout": 5})
        try:
            with engine.connect() as connection:
                if connection.scalar(sa.text("SELECT current_database()")) != name:
                    pytest.fail("temporary database connection selected the wrong database")
            monkeypatch.setenv(
                "MIGRATION_DATABASE_URL", target.render_as_string(hide_password=False)
            )
            config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
            head = ScriptDirectory.from_config(config).get_current_head()
            expected = {
                table.name for table in Base.metadata.tables.values() if table.schema == "study"
            }
            assert len(expected) == 40

            def check(upgraded: bool) -> None:
                with engine.connect() as connection:
                    inspector = sa.inspect(connection)
                    tables = set(inspector.get_table_names(schema="study")) - {"alembic_version"}
                    assert tables == (expected if upgraded else set())
                    version = connection.scalar(
                        sa.text("SELECT version_num FROM study.alembic_version")
                    )
                    assert version == (head if upgraded else None)
                    extensions = set(
                        connection.scalars(
                            sa.text(
                                "SELECT extname FROM pg_extension WHERE extname IN ('vector', 'citext')"
                            )
                        )
                    )
                    assert extensions == {"vector", "citext"}
                    assert ("study_graph" in inspector.get_schema_names()) == upgraded

            command.upgrade(config, "0003_bearer_auth")
            with engine.begin() as connection:
                owner = connection.scalar(sa.text("""INSERT INTO study.users(email, password_hash, display_name)
                    VALUES ('migration@example.test', 'unused', 'Migration') RETURNING id"""))
                kb_ids = [connection.scalar(sa.text("""INSERT INTO study.knowledge_bases(owner_user_id, name)
                    VALUES (:owner, :name) RETURNING id"""), {"owner": owner, "name": name}) for name in ("A", "B", "Deleted")]
                connection.execute(sa.text("UPDATE study.knowledge_bases SET deleted_at=now() WHERE id=:id"), {"id": kb_ids[2]})
                conversations = []
                for libraries in ([kb_ids[0]], kb_ids[:2], [], [kb_ids[2]]):
                    conversation = connection.scalar(sa.text("""INSERT INTO study.conversations(owner_user_id, mode)
                        VALUES (:owner, 'normal') RETURNING id"""), {"owner": owner})
                    conversations.append(conversation)
                    for kb in libraries:
                        connection.execute(sa.text("""INSERT INTO study.ai_workflow_runs(owner_user_id, conversation_id, knowledge_base_id, status)
                            VALUES (:owner, :conversation, :kb, 'completed')"""), {"owner": owner, "conversation": conversation, "kb": kb})
            command.upgrade(config, "head")
            with engine.connect() as connection:
                for conversation, expected_project in zip(conversations, [kb_ids[0], None, None, None], strict=True):
                    assert connection.scalar(sa.text("SELECT project_knowledge_base_id FROM study.conversations WHERE id=:id"), {"id": conversation}) == expected_project
            check(True)
            with engine.begin() as connection:
                for table in ("ai_workflow_runs", "conversations", "knowledge_bases"):
                    connection.execute(sa.text(f"DELETE FROM study.{table} WHERE owner_user_id=:owner"), {"owner": owner})
                connection.execute(sa.text("DELETE FROM study.users WHERE id=:owner"), {"owner": owner})
            command.downgrade(config, "base")
            check(False)
            command.upgrade(config, "head")
            check(True)
        finally:
            engine.dispose()
    finally:
        try:
            if created:
                with admin_engine.connect() as admin:
                    admin.exec_driver_sql(f"DROP DATABASE {quoted_name}")
        finally:
            admin_engine.dispose()
