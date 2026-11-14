import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, pool

from alembic import context
from study_platform.core.config import Settings
from study_platform.persistence import models  # noqa: F401
from study_platform.persistence.base import Base

config = context.config
load_dotenv(Settings.model_config["env_file"])
database_url = os.environ.get("MIGRATION_DATABASE_URL") or Settings().database_url
target_metadata = Base.metadata


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name == "study"
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        include_name=include_name,
        version_table_schema="study",
        compare_type=True,
    )
    with context.begin_transaction():
        context.execute("CREATE SCHEMA IF NOT EXISTS study")
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url, poolclass=pool.NullPool, hide_parameters=True)
    with engine.connect() as connection:
        # Bootstrap the version-table schema before Alembic creates its own table.
        # 0001 also creates schemas for offline SQL and fresh installations.
        connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS study")
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_name=include_name,
            version_table_schema="study",
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
