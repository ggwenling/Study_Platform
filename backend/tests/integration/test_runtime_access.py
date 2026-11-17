"""Runtime credentials must keep business data and graph state separated."""

import os

import psycopg
import pytest
from psycopg.errors import InsufficientPrivilege
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration


@pytest.fixture
def runtime_urls():
    if os.getenv("RUN_DB_TESTS") != "1":
        pytest.skip("set RUN_DB_TESTS=1 to run PostgreSQL tests")
    if make_url(os.environ["DATABASE_URL"]).username == "postgres":
        pytest.skip("runtime access checks require non-superuser DATABASE_URL")
    required = ("TEST_DATABASE_URL", "DATABASE_URL", "GRAPH_DATABASE_URL")
    if not all(os.getenv(key) for key in required):
        pytest.skip("test and runtime database URLs are required")
    test_database = make_url(os.environ["TEST_DATABASE_URL"]).database
    assert test_database and test_database.endswith("_test")
    return tuple(
        make_url(os.environ[key])
        .set(drivername="postgresql", database=test_database)
        .render_as_string(hide_password=False)
        for key in ("DATABASE_URL", "GRAPH_DATABASE_URL")
    )


def test_application_can_read_business_tables_but_cannot_read_checkpoints(runtime_urls):
    with psycopg.connect(runtime_urls[0], autocommit=True, connect_timeout=5) as connection:
        assert connection.execute("SELECT count(*) FROM study.users").fetchone()[0] >= 0
        assert not connection.execute(
            "SELECT has_schema_privilege(current_user, 'study', 'CREATE')"
        ).fetchone()[0]
        with pytest.raises(InsufficientPrivilege):
            connection.execute("SELECT * FROM study_graph.checkpoints LIMIT 0")


def test_graph_role_cannot_read_business_tables(runtime_urls):
    with psycopg.connect(runtime_urls[1], autocommit=True, connect_timeout=5) as connection:
        connection.execute("SELECT * FROM study_graph.checkpoints LIMIT 0")
        with pytest.raises(InsufficientPrivilege):
            connection.execute("SELECT * FROM study.users LIMIT 0")
