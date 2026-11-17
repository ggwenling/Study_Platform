"""Create CI PostgreSQL roles, disposable test database, and root .env for pytest."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy import URL

ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
TEST_DATABASE = "study_platform_test"


def _admin_connection():
    return psycopg.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", "5432")),
        dbname="postgres",
        user=os.environ.get("POSTGRES_USER", "postgres"),
        password=os.environ.get("POSTGRES_PASSWORD", "postgres"),
        autocommit=True,
        connect_timeout=10,
    )


def _url(user: str, password: str, database: str, driver: str) -> str:
    return URL.create(
        driver,
        username=user,
        password=password,
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", "5432")),
        database=database,
    ).render_as_string(hide_password=False)


def write_env(app_password: str, graph_password: str, jwt_secret: str) -> Path:
    admin_user = os.environ.get("POSTGRES_USER", "postgres")
    admin_password = os.environ.get("POSTGRES_PASSWORD", "postgres")
    values = {
        "AUTH_JWT_SECRET": jwt_secret,
        "DATABASE_URL": _url("study_app", app_password, TEST_DATABASE, "postgresql+psycopg"),
        "GRAPH_DATABASE_URL": _url("study_graph", graph_password, TEST_DATABASE, "postgresql"),
        "MIGRATION_DATABASE_URL": _url(
            admin_user, admin_password, TEST_DATABASE, "postgresql+psycopg"
        ),
        "TEST_DATABASE_URL": _url(admin_user, admin_password, TEST_DATABASE, "postgresql+psycopg"),
        "TEST_GRAPH_DATABASE_URL": _url(
            "study_graph", graph_password, TEST_DATABASE, "postgresql"
        ),
        "DEEPSEEK_API_KEY": "",
        "EMBEDDING_API_KEY": "",
        "RAG_QUERY_REWRITE_ENABLED": "false",
        "LEARNING_STUB_GRADER": "true",
    }
    env_path = ROOT / ".env"
    env_path.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    return env_path


def bootstrap_roles_and_database(app_password: str, graph_password: str) -> None:
    with _admin_connection() as connection:
        for role, password in (
            ("study_app", app_password),
            ("study_graph", graph_password),
        ):
            if connection.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
            ).fetchone():
                connection.execute(
                    sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(password)
                    )
                )
            else:
                connection.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} "
                        "NOSUPERUSER NOCREATEDB NOCREATEROLE"
                    ).format(sql.Identifier(role), sql.Literal(password))
                )

        connection.execute(
            sql.SQL(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = {} AND pid <> pg_backend_pid()"
            ).format(sql.Literal(TEST_DATABASE))
        )
        connection.execute(
            sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(TEST_DATABASE))
        )
        connection.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(
                sql.Identifier(TEST_DATABASE)
            )
        )
        connection.execute(
            sql.SQL("ALTER DATABASE {} SET timezone TO 'UTC'").format(
                sql.Identifier(TEST_DATABASE)
            )
        )

    admin_user = os.environ.get("POSTGRES_USER", "postgres")
    admin_password = os.environ.get("POSTGRES_PASSWORD", "postgres")
    with psycopg.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", "5432")),
        dbname=TEST_DATABASE,
        user=admin_user,
        password=admin_password,
        autocommit=True,
        connect_timeout=10,
    ) as connection:
        connection.execute("CREATE SCHEMA IF NOT EXISTS study")
        connection.execute("CREATE SCHEMA IF NOT EXISTS study_graph AUTHORIZATION study_graph")
        connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        connection.execute("GRANT USAGE ON SCHEMA study TO study_app")
        connection.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA study "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO study_app"
        )
        connection.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA study "
            "GRANT USAGE, SELECT ON SEQUENCES TO study_app"
        )


def apply_runtime_grants() -> None:
    admin_user = os.environ.get("POSTGRES_USER", "postgres")
    admin_password = os.environ.get("POSTGRES_PASSWORD", "postgres")
    with psycopg.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", "5432")),
        dbname=TEST_DATABASE,
        user=admin_user,
        password=admin_password,
        autocommit=True,
        connect_timeout=10,
    ) as connection:
        connection.execute("GRANT USAGE ON SCHEMA study TO study_app")
        connection.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA study TO study_app"
        )
        connection.execute(
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA study TO study_app"
        )
        connection.execute("REVOKE ALL ON SCHEMA study_graph FROM PUBLIC")
        connection.execute("REVOKE ALL ON SCHEMA study_graph FROM study_app")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--grants-only",
        action="store_true",
        help="Only refresh study/study_graph grants after migrations.",
    )
    args = parser.parse_args()

    app_password = os.environ.get("CI_STUDY_APP_PASSWORD", "ci-study-app-secret")
    graph_password = os.environ.get("CI_STUDY_GRAPH_PASSWORD", "ci-study-graph-secret")
    jwt_secret = os.environ.get(
        "AUTH_JWT_SECRET", "ci-jwt-secret-key-at-least-32-characters-long"
    )

    if args.grants_only:
        apply_runtime_grants()
        return

    bootstrap_roles_and_database(app_password, graph_password)
    env_path = write_env(app_password, graph_password, jwt_secret)
    print(f"Wrote {env_path}")

    subprocess.check_call(
        [sys.executable, str(BACKEND / "tests" / "prepare_database.py")],
        cwd=BACKEND,
    )
    apply_runtime_grants()
    print("PostgreSQL CI bootstrap complete.")


if __name__ == "__main__":
    main()