"""Add rerank connections, library retrieval settings and reindex jobs."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0016_rerank_and_kb_retrieval"
down_revision = "0015_note_lexical_search"
branch_labels = None
depends_on = None

_CHECKS = (
    ("model_connections", "ck_model_connections_kind", "kind",
     ("llm", "embedding", "vision"), "rerank"),
    ("ai_jobs", "ck_ai_jobs_job_type_values", "job_type",
     ("ingest", "notes", "quiz", "flashcards", "grade", "cleanup", "classification"), "reindex"),
    ("model_call_records", "ck_model_call_records_purpose_values", "purpose",
     ("chat", "learning_quiz", "learning_grade", "query_rewrite", "embedding", "classification",
      "connection_test", "vision_ingest", "vision_grade"), "rerank"),
)


def _replace_checks(upgrading):
    for table, name, column, values, added in _CHECKS:
        if upgrading:
            values = (*values, added)
        op.drop_constraint(name, table, schema="study", type_="check")
        op.create_check_constraint(name, table, f"{column} IN {values}", schema="study")


def upgrade():
    op.add_column("knowledge_bases", sa.Column("retrieval_settings", postgresql.JSONB()), schema="study")
    _replace_checks(True)


def downgrade():
    _replace_checks(False)
    op.drop_column("knowledge_bases", "retrieval_settings", schema="study")
