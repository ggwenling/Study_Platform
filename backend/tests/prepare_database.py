"""Initialize only the explicitly configured integration-test database."""

import os
from pathlib import Path

from alembic.config import Config
from dotenv import load_dotenv
from sqlalchemy.engine import make_url

from alembic import command
from study_platform.cli.setup_checkpointer import main as setup_checkpointer
from study_platform.core.config import get_settings

backend = Path(__file__).resolve().parents[1]
load_dotenv(backend.parent / ".env")
for key in ("TEST_DATABASE_URL", "TEST_GRAPH_DATABASE_URL"):
    url = os.environ.get(key)
    if not url or not (make_url(url).database or "").endswith("_test"):
        raise SystemExit(f"{key} must point to an explicit PostgreSQL _test database.")
os.environ["MIGRATION_DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ["GRAPH_DATABASE_URL"] = os.environ["TEST_GRAPH_DATABASE_URL"]
command.upgrade(Config(str(backend / "alembic.ini")), "head")
get_settings.cache_clear()
setup_checkpointer()
print("Test schemas initialized.")
