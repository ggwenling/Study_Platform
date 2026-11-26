"""Persist the personal text connection selected for each conversation."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0017_conversation_llm"
down_revision = "0016_rerank_and_kb_retrieval"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("conversations", sa.Column("llm_connection_id", postgresql.UUID()), schema="study")
    op.create_foreign_key(
        "fk_conversations_llm_connection", "conversations", "model_connections",
        ["llm_connection_id"], ["id"], source_schema="study", referent_schema="study", ondelete="RESTRICT",
    )


def downgrade():
    op.drop_constraint("fk_conversations_llm_connection", "conversations", schema="study", type_="foreignkey")
    op.drop_column("conversations", "llm_connection_id", schema="study")
