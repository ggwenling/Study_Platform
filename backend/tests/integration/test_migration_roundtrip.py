"""Exercise Alembic only in a disposable database created by this test."""

import os
from io import StringIO
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


@pytest.mark.parametrize("offline", [False, True], ids=["online", "offline"])
def test_migration_roundtrip(monkeypatch: pytest.MonkeyPatch, offline: bool) -> None:
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
            assert len(expected) == 43

            def check(upgraded: bool) -> None:
                with engine.connect() as connection:
                    inspector = sa.inspect(connection)
                    tables = set(inspector.get_table_names(schema="study")) - {"alembic_version"}
                    assert tables == (expected if upgraded else set())
                    material_columns = {
                        column["name"] for column in inspector.get_columns("materials", schema="study")
                    } if upgraded else set()
                    assert ("source_url" in material_columns) is upgraded
                    if upgraded:
                        notes = {column["name"] for column in inspector.get_columns("notes", schema="study")}
                        assert "lexical" in notes
                        assert any(index["name"] == "ix_notes_lexical" for index in inspector.get_indexes("notes", schema="study"))
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
            command.upgrade(config, "0009_model_call_records")
            with engine.begin() as connection:
                connection.execute(sa.text("INSERT INTO study.courses(id, owner_user_id, name) "
                                           "VALUES (:kb, :owner, 'Notes')"), {"kb": kb_ids[0], "owner": owner})
                note_id = connection.scalar(sa.text("""
                    INSERT INTO study.notes(owner_user_id, course_id, title, note_type, content_md,
                        generated_content_md, status, source_scope, generator_profile, prompt_version)
                    VALUES (:owner, :kb, '雪松仓库', 'summary', '向量模型 QZ-731', '', 'ready',
                        '{}'::jsonb, 'test', 'test') RETURNING id
                """), {"kb": kb_ids[0], "owner": owner})

                def add_material(profile: str, *, deleted: bool = False):
                    material = connection.execute(sa.insert(models.Material).values(
                        owner_user_id=owner, knowledge_base_id=kb_ids[0], title=profile,
                        kind="other", status="active", deleted_at=sa.func.now() if deleted else None,
                    ).returning(models.Material.id)).scalar_one()
                    version = connection.execute(sa.insert(models.MaterialVersion).values(
                        material_id=material, version_no=1, is_current=True, source_mime="text/plain",
                        source_sha256="a" * 64, byte_size=1, object_key=profile, parse_status="ready",
                    ).returning(models.MaterialVersion.id)).scalar_one()
                    index = connection.execute(sa.insert(models.IndexVersion).values(
                        material_version_id=version, generation=1, pipeline_profile="legacy",
                        embedding_profile=profile, status="ready", is_active=True,
                        ready_at=sa.func.now() + sa.text("interval '1 hour'") if deleted else sa.func.now(),
                    ).returning(models.IndexVersion.id)).scalar_one()
                    return material, version, index

                materials, versions, indexes = zip(
                    add_material("legacy-embedding:1024"),
                    add_material("deleted-embedding:1024", deleted=True),
                    strict=True,
                )

            from study_platform.core import config as settings_module

            # Exercise the same quoted JSON value in the online and generated SQL paths.
            platform = settings_module.get_settings().model_copy(update={
                "embedding_model": "platform-'quoted'",
                "embedding_base_url": "https://platform.example/v1/",
            })
            monkeypatch.setattr(settings_module, "get_settings", lambda: platform)
            if offline:
                output = StringIO()
                script = Config(config.config_file_name, output_buffer=output)
                command.upgrade(script, "0009_model_call_records:head", sql=True)
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                    connection.exec_driver_sql(output.getvalue())
            else:
                command.upgrade(config, "head")
            with engine.connect() as connection:
                bindings = {}
                for kb_id, personal_id, binding in connection.execute(sa.text(
                    "SELECT id, embedding_connection_id, embedding_binding FROM study.knowledge_bases"
                )):
                    assert personal_id is None and binding["dimensions"] == 1024
                    assert binding["base_url"] == "https://platform.example/v1"
                    bindings[kb_id] = binding
                assert connection.scalar(sa.text(
                    "SELECT count(*) FROM study.materials WHERE source_url IS NULL"
                )) == len(materials)
                assert bindings[kb_ids[0]]["profile"] == "legacy-embedding:1024"
                assert bindings[kb_ids[0]]["model"] == "legacy-embedding"
                assert bindings[kb_ids[1]]["model"] == "platform-'quoted'"
                assert bindings[kb_ids[1]]["profile"] == "platform-'quoted':1024"
                assert connection.scalar(sa.text("SELECT is_active FROM study.index_versions WHERE id=:id"), {"id": indexes[0]})
                for conversation, expected_project in zip(conversations, [kb_ids[0], None, None, None], strict=True):
                    assert connection.scalar(sa.text("SELECT project_knowledge_base_id FROM study.conversations WHERE id=:id"), {"id": conversation}) == expected_project
            check(True)
            if not offline:
                with engine.connect() as connection:
                    assert connection.scalar(sa.text("SELECT lexical @@ plainto_tsquery('simple', '雪松') "
                        "FROM study.notes WHERE id=:id"), {"id": note_id})
            with engine.begin() as connection:
                vision_connection = connection.scalar(sa.text(
                    "INSERT INTO study.model_connections(owner_user_id, kind, name, versions) "
                    "VALUES (:owner, 'vision', 'Vision', '{}'::jsonb) RETURNING id"
                ), {"owner": owner})
                connection.execute(sa.text(
                    "INSERT INTO study.model_call_records(owner_user_id, purpose, model_name, success) "
                    "VALUES (:owner, 'vision_ingest', 'vision-test', true)"
                ), {"owner": owner})
            with pytest.raises(RuntimeError, match="vision connections or usage"):
                command.downgrade(config, "0013_material_source_url")
            check(True)
            with engine.begin() as connection:
                connection.execute(sa.text(
                    "DELETE FROM study.model_call_records WHERE owner_user_id=:owner "
                    "AND purpose='vision_ingest'"
                ), {"owner": owner})
                connection.execute(sa.text(
                    "DELETE FROM study.model_connections WHERE id=:id"
                ), {"id": vision_connection})
            with engine.begin() as connection:
                connection.execute(sa.delete(models.IndexVersion).where(models.IndexVersion.id.in_(indexes)))
                connection.execute(sa.delete(models.MaterialVersion).where(models.MaterialVersion.id.in_(versions)))
                connection.execute(sa.delete(models.Material).where(models.Material.id.in_(materials)))
                for table in ("ai_workflow_runs", "conversations", "notes", "courses", "knowledge_bases"):
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


def test_lexical_backfill_handles_symbol_only_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
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

    name = f"study_lexical_backfill_{uuid4().hex}_test"
    url = url.set(drivername="postgresql+psycopg")
    admin_engine = sa.create_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 5},
    )
    created = False
    try:
        with admin_engine.connect() as admin:
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
            monkeypatch.setenv(
                "MIGRATION_DATABASE_URL", target.render_as_string(hide_password=False)
            )
            config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
            command.upgrade(config, "0007_learning_verdict")

            symbol_ids: list[object] = []
            with engine.begin() as connection:
                owner = connection.scalar(
                    sa.text(
                        """
                        INSERT INTO study.users(email, password_hash, display_name)
                        VALUES ('backfill@example.test', 'unused', 'Backfill')
                        RETURNING id
                        """
                    )
                )
                kb = connection.scalar(
                    sa.text(
                        """
                        INSERT INTO study.knowledge_bases(owner_user_id, name)
                        VALUES (:owner, 'Backfill KB')
                        RETURNING id
                        """
                    ),
                    {"owner": owner},
                )
                material = connection.scalar(
                    sa.text(
                        """
                        INSERT INTO study.materials(owner_user_id, knowledge_base_id, title, kind, status)
                        VALUES (:owner, :kb, 'Symbols', 'other', 'active')
                        RETURNING id
                        """
                    ),
                    {"owner": owner, "kb": kb},
                )
                version = connection.scalar(
                    sa.text(
                        """
                        INSERT INTO study.material_versions(
                            material_id, version_no, is_current, source_mime,
                            source_sha256, byte_size, object_key, parse_status
                        )
                        VALUES (:material, 1, true, 'text/plain', :sha, 10, 'backfill-key', 'ready')
                        RETURNING id
                        """
                    ),
                    {"material": material, "sha": "b" * 64},
                )
                index_version = connection.scalar(
                    sa.text(
                        """
                        INSERT INTO study.index_versions(
                            material_version_id, generation, pipeline_profile,
                            embedding_profile, status, is_active, ready_at
                        )
                        VALUES (:version, 1, 'legacy', 'legacy:1024', 'ready', true, now())
                        RETURNING id
                        """
                    ),
                    {"version": version},
                )
                samples = ["---", "... *** !!!", "   ", "雪松仓库 QZ-731"]
                for ordinal, text in enumerate(samples):
                    chunk_id = connection.scalar(
                        sa.text(
                            """
                            INSERT INTO study.material_chunks(
                                index_version_id, material_version_id, ordinal,
                                text, text_sha256, locator, lexical
                            )
                            VALUES (:index_id, :version_id, :ordinal, :text, :sha, '{}'::jsonb, NULL)
                            RETURNING id
                            """
                        ),
                        {
                            "index_id": index_version,
                            "version_id": version,
                            "ordinal": ordinal,
                            "text": text,
                            "sha": f"{ordinal:064x}",
                        },
                    )
                    if ordinal < 3:
                        symbol_ids.append(chunk_id)
                    else:
                        normal_id = chunk_id

            command.upgrade(config, "0008_lexical_backfill")

            with engine.connect() as connection:
                null_count = connection.scalar(
                    sa.text("SELECT COUNT(*) FROM study.material_chunks WHERE lexical IS NULL")
                )
                assert null_count == 0
                for chunk_id in symbol_ids:
                    lexical_text = connection.scalar(
                        sa.text("SELECT lexical::text FROM study.material_chunks WHERE id = :id"),
                        {"id": chunk_id},
                    )
                    assert lexical_text == ""
                matched_cn = connection.scalar(
                    sa.text(
                        """
                        SELECT lexical @@ plainto_tsquery('simple', '雪松')
                        FROM study.material_chunks
                        WHERE id = :id
                        """
                    ),
                    {"id": normal_id},
                )
                matched_num = connection.scalar(
                    sa.text(
                        """
                        SELECT lexical @@ plainto_tsquery('simple', '731')
                        FROM study.material_chunks
                        WHERE id = :id
                        """
                    ),
                    {"id": normal_id},
                )
                assert matched_cn is True
                assert matched_num is True
        finally:
            engine.dispose()
    finally:
        try:
            if created:
                with admin_engine.connect() as admin:
                    admin.exec_driver_sql(f"DROP DATABASE {quoted_name}")
        finally:
            admin_engine.dispose()
