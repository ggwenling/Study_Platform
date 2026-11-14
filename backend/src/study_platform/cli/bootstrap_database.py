"""Create fresh project databases and local credentials using an existing administrator."""

import argparse
import getpass
import secrets
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy import URL


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-user", default="postgres")
    args = parser.parse_args()
    env_path = Path(__file__).resolve().parents[4] / ".env"
    if env_path.exists():
        raise SystemExit(".env already exists; inspect the existing project configuration first.")

    for _ in range(2):
        password = getpass.getpass("Existing PostgreSQL administrator password: ")
        try:
            connection = psycopg.connect(
                host="127.0.0.1",
                port=5432,
                dbname="postgres",
                user=args.admin_user,
                password=password,
                autocommit=True,
                connect_timeout=5,
            )
            break
        except psycopg.OperationalError:
            print("Connection rejected. Check the service, administrator name, and password.")
    else:
        raise SystemExit("No valid administrator connection; no database changes made.")

    databases = ("study_platform", "study_platform_test")
    roles = {"study_app": secrets.token_urlsafe(32), "study_graph": secrets.token_urlsafe(32)}
    with connection:
        is_superuser = connection.execute(
            "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
        ).fetchone()[0]
        if not is_superuser:
            raise SystemExit("A superuser is required for the initial extension installation.")
        existing_databases = connection.execute(
            "SELECT datname FROM pg_database WHERE datname = ANY(%s)", (list(databases),)
        ).fetchall()
        existing_roles = connection.execute(
            "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", (list(roles),)
        ).fetchall()
        if existing_databases or existing_roles:
            raise SystemExit("Project database or role names already exist; nothing overwritten.")
        for role, role_password in roles.items():
            connection.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE"
                ).format(
                    sql.Identifier(role),
                    sql.Literal(role_password),
                )
            )
        for database in databases:
            connection.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(
                    sql.Identifier(database)
                )
            )
            connection.execute(
                sql.SQL("ALTER DATABASE {} SET timezone TO 'UTC'").format(sql.Identifier(database))
            )

    for database in databases:
        with psycopg.connect(
            host="127.0.0.1",
            port=5432,
            dbname=database,
            user=args.admin_user,
            password=password,
            connect_timeout=5,
        ) as project_connection:
            project_connection.execute("CREATE SCHEMA study")
            project_connection.execute("CREATE SCHEMA study_graph AUTHORIZATION study_graph")
            project_connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            project_connection.execute("GRANT USAGE ON SCHEMA study TO study_app")
            project_connection.execute(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA study "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO study_app"
            )

    def url(user: str, secret: str, database: str, driver: str) -> str:
        return URL.create(
            driver, username=user, password=secret, host="127.0.0.1", port=5432, database=database
        ).render_as_string(hide_password=False)

    values = {
        "AUTH_JWT_SECRET": secrets.token_urlsafe(32),
        "DATABASE_URL": url("study_app", roles["study_app"], databases[0], "postgresql+psycopg"),
        "GRAPH_DATABASE_URL": url("study_graph", roles["study_graph"], databases[0], "postgresql"),
        "MIGRATION_DATABASE_URL": url(
            args.admin_user, password, databases[0], "postgresql+psycopg"
        ),
        "TEST_DATABASE_URL": url(args.admin_user, password, databases[1], "postgresql+psycopg"),
        "TEST_GRAPH_DATABASE_URL": url(
            "study_graph", roles["study_graph"], databases[1], "postgresql"
        ),
    }
    env_path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8"
    )
    print(
        "Created isolated project databases and runtime roles; credentials saved to ignored .env."
    )


if __name__ == "__main__":
    main()
