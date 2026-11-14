"""Group conversations by a knowledge-base project without restricting turn scope."""

import sqlalchemy as sa

from alembic import op

revision = "0004_project_conversations"
down_revision = "0003_bearer_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("project_knowledge_base_id", sa.Uuid()), schema="study")
    op.create_foreign_key(
        "fk_conversations_project_owner", "conversations", "knowledge_bases",
        ["project_knowledge_base_id", "owner_user_id"], ["id", "owner_user_id"],
        source_schema="study", referent_schema="study", ondelete="RESTRICT",
    )
    op.create_index("ix_conversations_owner_project_created", "conversations",
                    ["owner_user_id", "project_knowledge_base_id", sa.text("created_at DESC")], schema="study")
    op.execute("""
        UPDATE study.conversations c SET project_knowledge_base_id = k.id
        FROM study.knowledge_bases k, (
            SELECT conversation_id, (array_agg(DISTINCT knowledge_base_id))[1] AS kb_id
            FROM study.ai_workflow_runs
            WHERE knowledge_base_id IS NOT NULL
            GROUP BY conversation_id HAVING count(DISTINCT knowledge_base_id) = 1
        ) r
        WHERE c.id = r.conversation_id AND k.id = r.kb_id
          AND k.owner_user_id = c.owner_user_id AND k.deleted_at IS NULL
    """)


def downgrade() -> None:
    op.drop_index("ix_conversations_owner_project_created", table_name="conversations", schema="study")
    op.drop_constraint("fk_conversations_project_owner", "conversations", schema="study")
    op.drop_column("conversations", "project_knowledge_base_id", schema="study")
