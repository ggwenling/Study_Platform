"""Real PostgreSQL checks for retained evaluation indexes.

Vectors are handmade. This module does not call an embedding or chat API and
does not read a production DSN. Set RUN_DB_TESTS=1 and point TEST_DATABASE_URL
at a disposable PostgreSQL *_test database whose role can create databases.
"""

import hashlib
import os
import sys
from uuid import uuid4

import dotenv
import pytest
import sqlalchemy as sa

from study_platform.cli.evaluate_rag import isolated_database
from study_platform.core.config import Settings, get_settings
from study_platform.services.chunking import SEMANTIC_PROFILE
from study_platform.services.eval_index_reuse import (
    EMBEDDING_DIM,
    IndexReuseError,
    assert_database_name,
    build_manifest,
    code_schema_revision,
    import_index,
    new_database_name,
    read_snapshot,
    retained_database,
    reuse_target_url,
    verify_index_contents,
)
from study_platform.services.eval_index_reuse import (
    test_database_url as parse_test_database_url,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1" or not os.getenv("TEST_DATABASE_URL"),
    reason="set RUN_DB_TESTS=1 and TEST_DATABASE_URL to a disposable *_test database",
)


@pytest.fixture(autouse=True)
def _keep_secret_files_unread(monkeypatch):
    """Do not open an application env file. The test URL comes from the process environment."""
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *_args, **_kwargs: False)
    env_module = sys.modules.get("alembic.env")
    if env_module is not None:
        monkeypatch.setattr(env_module, "load_dotenv", lambda *_args, **_kwargs: False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    raw = os.getenv("TEST_DATABASE_URL")
    if raw:
        monkeypatch.setenv("DATABASE_URL", raw)
        monkeypatch.setenv("MIGRATION_DATABASE_URL", raw)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _url():
    raw = os.environ["TEST_DATABASE_URL"]
    url = parse_test_database_url(raw)
    if not (url.database or "").endswith("_test"):
        raise RuntimeError("TEST_DATABASE_URL must name a *_test database")
    return raw, url


def _admin():
    _raw, url = _url()
    return sa.create_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )


def _exists(name):
    engine = _admin()
    try:
        with engine.connect() as connection:
            return bool(
                connection.scalar(
                    sa.text("SELECT 1 FROM pg_database WHERE datname = :name"),
                    {"name": name},
                )
            )
    finally:
        engine.dispose()


def _drop(name):
    assert_database_name(name)
    engine = _admin()
    try:
        with engine.connect() as connection:
            connection.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {_quote(name)}")
    finally:
        engine.dispose()


def _quote(name):
    assert_database_name(name)
    return f'"{name}"'


def _synthetic(database_name, owner_role):
    owner, base, material, version, index = (str(uuid4()) for _ in range(5))
    text = "synthetic span alpha"
    digest = hashlib.sha256(text.encode()).hexdigest()
    profile = "synthetic-embed:1024"
    snapshot = {
        "database_name": database_name,
        "database_owner_role": owner_role,
        "current_user": owner_role,
        "schema_revision": code_schema_revision(),
        "user_ids": [owner],
        "embedding_profile": profile,
        "knowledge_bases": [
            {"id": base, "name": "kb", "owner_user_id": owner, "deleted_at": None}
        ],
        "materials": [
            {
                "id": material,
                "owner_user_id": owner,
                "knowledge_base_id": base,
                "title": "Notes",
                "version_id": version,
                "source_sha256": digest,
                "is_current": True,
                "source_mime": "text/plain",
                "byte_size": len(text.encode()),
            }
        ],
        "indexes": [
            {
                "id": index,
                "material_version_id": version,
                "pipeline_profile": SEMANTIC_PROFILE,
                "embedding_profile": profile,
                "embedding_dim": EMBEDDING_DIM,
                "chunk_count": 1,
                "status": "ready",
                "is_active": True,
                "generation": 1,
                "actual_chunks": 1,
                "embedding_rows": 1,
                "model_profiles": [profile],
                "vector_dim_min": EMBEDDING_DIM,
                "vector_dim_max": EMBEDDING_DIM,
            }
        ],
    }
    scopes = {
        "kb": {
            "knowledge_base_id": base,
            "index_version_ids": [index],
            "intent": "qa",
            "answer_mode": "knowledge_only",
        }
    }
    manifest = build_manifest(
        snapshot=snapshot,
        scopes=scopes,
        documents={version: "doc-1"},
        material_hashes={"doc-1": digest},
        dataset_sha256="cd" * 32,
        materials_sha256="ef" * 32,
        embedding_model="synthetic-embed",
    )
    vector = [0.25 if item == 0 else 0.0 for item in range(EMBEDDING_DIM)]
    records = [
        {
            "document_id": "doc-1",
            "material_id": material,
            "material_version_id": version,
            "knowledge_base_id": base,
            "knowledge_base_name": "kb",
            "title": "Notes",
            "source_sha256": digest,
            "source_mime": "text/plain",
            "byte_size": len(text.encode()),
            "index_version_id": index,
            "chunk_count": 1,
            "chunks": [
                {
                    "ordinal": 0,
                    "text": text,
                    "text_sha256": digest,
                    "locator": {"line": 1},
                    "embedding": vector,
                }
            ],
        }
    ]
    return manifest, records, vector


def test_failed_retain_drops_the_database(tmp_path):
    created = None
    with pytest.raises(RuntimeError, match="build failed"):
        with isolated_database(tmp_path, retain=True) as factory:
            with factory() as db:
                created = db.scalar(sa.text("SELECT current_database()"))
            assert_database_name(created)
            raise RuntimeError("build failed")
    assert created
    assert not _exists(created)


def test_retained_index_is_read_only_and_rejects_a_bad_revision(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("embedding API must not be called")

    monkeypatch.setattr("study_platform.services.models.embed_texts", forbidden)
    monkeypatch.setattr("study_platform.cli.evaluate_rag.embed_texts", forbidden)
    raw, url = _url()
    kept = None
    try:
        with isolated_database(tmp_path, retain=True) as factory:
            with factory() as db:
                kept = db.scalar(sa.text("SELECT current_database()"))
                owner_role = db.scalar(sa.text("SELECT current_user"))
            assert_database_name(kept)
            manifest, records, vector = _synthetic(kept, owner_role)
            import_index(factory, manifest, records)
            stored = read_snapshot(factory)
            verify_index_contents(
                manifest,
                stored,
                embedding_model="synthetic-embed",
                schema_revision=code_schema_revision(),
            )
        assert _exists(kept)
        with retained_database(
            manifest,
            application_database="study_platform",
            shared_test_database=url.database,
            test_database_url_raw=raw,
        ) as factory:
            snapshot = read_snapshot(factory)
            verify_index_contents(
                manifest,
                snapshot,
                embedding_model="synthetic-embed",
                schema_revision=code_schema_revision(),
            )
            with factory() as db:
                assert db.scalar(sa.text("SHOW default_transaction_read_only")) == "on"
                found = db.execute(
                    sa.text(
                        """
                        SELECT c.text, vector_dims(e.embedding), e.embedding::text
                        FROM study.material_chunks AS c
                        JOIN study.chunk_embeddings AS e ON e.chunk_id = c.id
                        """
                    )
                ).one()
                assert found[0] == "synthetic span alpha"
                assert found[1] == EMBEDDING_DIM
                assert found[2].startswith("[0.25")
                with pytest.raises(sa.exc.InternalError, match="read-only"):
                    db.execute(sa.text("CREATE TABLE study.reuse_probe(id int)"))
                    db.commit()
            mismatched = dict(snapshot, schema_revision="0000_not_a_head")
            with pytest.raises(IndexReuseError, match="schema revision"):
                verify_index_contents(
                    manifest,
                    mismatched,
                    embedding_model="synthetic-embed",
                    schema_revision=code_schema_revision(),
                )
            assert vector[0] == 0.25 and len(vector) == EMBEDDING_DIM
    finally:
        if kept and _exists(kept):
            _drop(kept)


def _ensure_other_role():
    engine = _admin()
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(
                "DO $$ BEGIN "
                "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'study_eval_other') "
                "THEN CREATE ROLE study_eval_other NOLOGIN; END IF; "
                "END $$"
            )
    finally:
        engine.dispose()


def test_owner_and_name_boundaries_reject_before_reuse():
    raw, url = _url()
    with pytest.raises(IndexReuseError, match="database name"):
        reuse_target_url(raw, url.database)
    with pytest.raises(IndexReuseError, match="database name"):
        reuse_target_url(raw, "postgres")
    _ensure_other_role()
    foreign = new_database_name()
    engine = _admin()
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(
                f"CREATE DATABASE {_quote(foreign)} OWNER study_eval_other"
            )
    finally:
        engine.dispose()
    try:
        manifest, _records, _vector = _synthetic(foreign, "study_eval_other")
        with pytest.raises(IndexReuseError, match="database owner"):
            with retained_database(
                manifest,
                application_database="study_platform",
                shared_test_database=url.database,
                test_database_url_raw=raw,
            ):
                raise AssertionError("foreign owner must not be reused")
        with pytest.raises(IndexReuseError, match="application database"):
            with retained_database(
                manifest,
                application_database=foreign,
                shared_test_database=url.database,
                test_database_url_raw=raw,
            ):
                raise AssertionError("application database must not be reused")
    finally:
        if _exists(foreign):
            _drop(foreign)
