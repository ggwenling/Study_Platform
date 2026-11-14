"""Migrate legacy course ownership without losing legacy records."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.engine import make_url

from alembic import command

pytestmark = pytest.mark.integration


def test_legacy_material_mapping_and_nonempty_downgrade(monkeypatch):
    if os.getenv("RUN_DB_TESTS") != "1" or not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("explicit PostgreSQL test database required")
    url = make_url(os.environ["TEST_DATABASE_URL"])
    if not (url.database or "").endswith("_test") or {"dbname", "service", "servicefile"} & set(
        url.query
    ):
        pytest.fail("isolated *_test database required")
    url = url.set(drivername="postgresql+psycopg")
    name = f"study_personal_migration_{uuid4().hex}_test"
    admin = sa.create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    created = False
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
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        command.upgrade(config, "0001_initial_schema")
        engine = sa.create_engine(target)
        try:
            user, course, material = uuid4(), uuid4(), uuid4()
            with engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "INSERT INTO study.users (id,email,password_hash,display_name) VALUES (:id,:email,'hash','Legacy')"
                    ),
                    {"id": user, "email": f"{user}@example.test"},
                )
                connection.execute(
                    sa.text(
                        "INSERT INTO study.courses (id,owner_user_id,name,description) VALUES (:id,:owner,'Legacy course','Keep this')"
                    ),
                    {"id": course, "owner": user},
                )
                connection.execute(
                    sa.text(
                        "INSERT INTO study.materials (id,owner_user_id,course_id,title,kind) VALUES (:id,:owner,:course,'Legacy material','notes')"
                    ),
                    {"id": material, "owner": user, "course": course},
                )
            command.upgrade(config, "head")
            with engine.connect() as connection:
                kb = connection.execute(
                    sa.text("SELECT id,owner_user_id,name,description FROM study.knowledge_bases")
                ).one()
                assert tuple(kb) == (course, user, "Legacy course", "Keep this")
                row = connection.execute(
                    sa.text("SELECT id,course_id,knowledge_base_id FROM study.materials")
                ).one()
                assert tuple(row) == (material, course, course)
            with pytest.raises(sa.exc.DBAPIError, match="requires empty business domain"):
                command.downgrade(config, "0001_initial_schema")
            with engine.connect() as connection:
                assert connection.scalar(sa.text("SELECT count(*) FROM study.materials")) == 1
                assert (
                    connection.scalar(sa.text("SELECT version_num FROM study.alembic_version"))
                    == "0002_personal_rag"
                )
        finally:
            engine.dispose()
    finally:
        if created:
            with admin.connect() as connection:
                connection.exec_driver_sql(f"DROP DATABASE {quoted}")
        admin.dispose()
