"""Track model invocations per user without prompt or answer content."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0009_model_call_records"
down_revision = "0008_lexical_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_call_records",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("model_name", sa.String(120), nullable=False),
        sa.Column("used_fallback", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("usage", postgresql.JSONB(), nullable=True),
        sa.Column("elapsed_ms", sa.Numeric(12, 3), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "purpose IN ('chat', 'learning_quiz', 'learning_grade', 'query_rewrite', "
            "'embedding', 'classification')",
            name="ck_model_call_records_purpose_values",
        ),
        sa.CheckConstraint(
            "elapsed_ms IS NULL OR elapsed_ms >= 0",
            name="ck_model_call_records_elapsed",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["study.users.id"],
            name="fk_model_call_records_owner_user_id",
            ondelete="RESTRICT",
        ),
        schema="study",
    )
    op.create_index(
        "ix_model_call_records_owner_created",
        "model_call_records",
        ["owner_user_id", sa.text("created_at DESC")],
        unique=False,
        schema="study",
    )


def downgrade() -> None:
    op.drop_index("ix_model_call_records_owner_created", table_name="model_call_records", schema="study")
    op.drop_table("model_call_records", schema="study")