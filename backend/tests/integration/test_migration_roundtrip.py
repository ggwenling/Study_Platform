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
            assert len(expected) == 37

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

            command.upgrade(config, "head")
            check(True)
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
