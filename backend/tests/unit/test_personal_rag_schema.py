"""Personal RAG metadata and offline Alembic regression checks."""

from io import StringIO
from pathlib import Path

from alembic.config import Config
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from alembic import command
from study_platform.persistence.models import (
    AiWorkflowRun,
    Conversation,
    KnowledgeBase,
    Material,
    Message,
)


def test_personal_rag_schema_contract():
    assert not Material.__table__.c.knowledge_base_id.nullable
    assert Material.__table__.c.course_id.nullable
    assert Conversation.__table__.c.course_id.nullable
    assert AiWorkflowRun.__table__.c.graph_name.nullable
    assert AiWorkflowRun.__table__.c.fencing.server_default.arg.text == "0"
    assert {i.name for i in Message.__table__.indexes} >= {
        "ix_messages_user_run",
        "ix_messages_assistant_run",
    }
    for model in (KnowledgeBase, Material, Conversation, AiWorkflowRun):
        assert "CREATE TABLE study." in str(
            CreateTable(model.__table__).compile(dialect=postgresql.dialect())
        )


def test_personal_rag_upgrade_offline(monkeypatch):
    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL", "postgresql+psycopg://offline:offline@localhost/offline_test"
    )
    output = StringIO()
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "0001_initial_schema:head", sql=True)
    sql = output.getvalue()
    assert "INSERT INTO study.knowledge_bases" in sql
    assert "UPDATE study.materials SET knowledge_base_id=course_id" in sql
    assert "source_scope->>'intent' IN ('qa', 'summary')" in sql
