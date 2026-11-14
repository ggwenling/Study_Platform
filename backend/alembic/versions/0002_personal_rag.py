"""Add personal knowledge bases and durable RAG execution fields."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002_personal_rag"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_bases",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_knowledge_bases_id_owner_user_id"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["study.users.id"],
            name="fk_knowledge_bases_owner_user_id",
            ondelete="RESTRICT",
        ),
        schema="study",
    )
    op.execute(
        "INSERT INTO study.knowledge_bases (id,owner_user_id,name,description,created_at,updated_at,deleted_at) SELECT id,owner_user_id,name,description,created_at,updated_at,deleted_at FROM study.courses"
    )
    op.add_column(
        "materials", sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True)), schema="study"
    )
    op.execute("UPDATE study.materials SET knowledge_base_id=course_id")
    op.alter_column("materials", "knowledge_base_id", nullable=False, schema="study")
    op.create_foreign_key(
        "fk_materials_knowledge_base_owner",
        "materials",
        "knowledge_bases",
        ["knowledge_base_id", "owner_user_id"],
        ["id", "owner_user_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    for table in ("materials", "conversations"):
        op.alter_column(table, "course_id", nullable=True, schema="study")
    for column in (
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True)),
        sa.Column("answer_mode", sa.String(24)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("fencing", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    ):
        op.add_column("ai_workflow_runs", column, schema="study")
    op.create_foreign_key(
        "fk_ai_workflow_runs_knowledge_base_owner",
        "ai_workflow_runs",
        "knowledge_bases",
        ["knowledge_base_id", "owner_user_id"],
        ["id", "owner_user_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_ai_workflow_runs_answer_mode",
        "ai_workflow_runs",
        "answer_mode IN ('knowledge_only', 'with_general')",
        schema="study",
    )
    for column in ("graph_name", "graph_version", "thread_id", "checkpoint_namespace"):
        op.alter_column("ai_workflow_runs", column, nullable=True, schema="study")
    op.create_index(
        "ix_messages_user_run",
        "messages",
        ["workflow_run_id"],
        unique=True,
        schema="study",
        postgresql_where=sa.text("role = 'user' AND workflow_run_id IS NOT NULL"),
    )
    op.create_index(
        "ix_ai_workflow_runs_active_rag",
        "ai_workflow_runs",
        ["conversation_id"],
        unique=True,
        schema="study",
        postgresql_where=sa.text(
            "status IN ('created', 'running') AND source_scope->>'intent' IN ('qa', 'summary')"
        ),
    )


def downgrade() -> None:
    # Refuse before changing anything: no new-domain data may be silently discarded.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM study.materials) OR EXISTS (SELECT 1 FROM study.conversations)
        OR EXISTS (SELECT 1 FROM study.knowledge_bases) OR EXISTS (SELECT 1 FROM study.ai_workflow_runs)
        THEN RAISE EXCEPTION 'personal RAG downgrade requires empty business domain'; END IF;
        END $$""")
    op.drop_index("ix_ai_workflow_runs_active_rag", table_name="ai_workflow_runs", schema="study")
    op.drop_index("ix_messages_user_run", table_name="messages", schema="study")
    op.drop_constraint(
        "ck_ai_workflow_runs_answer_mode", "ai_workflow_runs", schema="study", type_="check"
    )
    op.drop_constraint(
        "fk_ai_workflow_runs_knowledge_base_owner",
        "ai_workflow_runs",
        schema="study",
        type_="foreignkey",
    )
    for column in ("knowledge_base_id", "answer_mode", "lease_expires_at", "fencing"):
        op.drop_column("ai_workflow_runs", column, schema="study")
    for column in ("graph_name", "graph_version", "thread_id", "checkpoint_namespace"):
        op.alter_column("ai_workflow_runs", column, nullable=False, schema="study")
    for table in ("materials", "conversations"):
        op.alter_column(table, "course_id", nullable=False, schema="study")
    op.drop_constraint(
        "fk_materials_knowledge_base_owner", "materials", schema="study", type_="foreignkey"
    )
    op.drop_column("materials", "knowledge_base_id", schema="study")
    op.drop_table("knowledge_bases", schema="study")
