"""Persist library classifications and fragment retrieval preferences."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0005_library_detail"
down_revision = "0004_project_conversations"
branch_labels = None
depends_on = None

JOB_TYPES = "'ingest', 'notes', 'quiz', 'flashcards', 'grade', 'cleanup'"


def change_job_types(types):
    op.drop_constraint("ck_ai_jobs_job_type_values", "ai_jobs", schema="study", type_="check")
    op.create_check_constraint("ck_ai_jobs_job_type_values", "ai_jobs",
                               f"job_type IN ({types})", schema="study")


def upgrade():
    op.add_column("materials", sa.Column("topic", sa.String(80)), schema="study")
    op.add_column("materials", sa.Column("tags", JSONB, server_default=sa.text("'[]'::jsonb"),
                                         nullable=False), schema="study")
    op.add_column("knowledge_bases", sa.Column("last_synced_at", sa.DateTime(timezone=True)), schema="study")
    op.add_column("material_chunks", sa.Column("retrieval_enabled", sa.Boolean, server_default=sa.true(),
                                                nullable=False), schema="study")
    change_job_types(f"{JOB_TYPES}, 'classification'")


def downgrade():
    op.execute("DELETE FROM study.ai_job_attempts WHERE job_id IN "
               "(SELECT id FROM study.ai_jobs WHERE job_type = 'classification')")
    op.execute("DELETE FROM study.ai_jobs WHERE job_type = 'classification'")
    change_job_types(JOB_TYPES)
    op.drop_column("material_chunks", "retrieval_enabled", schema="study")
    op.drop_column("knowledge_bases", "last_synced_at", schema="study")
    op.drop_column("materials", "tags", schema="study")
    op.drop_column("materials", "topic", schema="study")
