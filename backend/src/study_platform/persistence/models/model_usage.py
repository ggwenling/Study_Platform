"""Per-user model invocation metrics without prompt or content."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from study_platform.persistence.base import Base


class ModelCallRecord(Base):
    __tablename__ = "model_call_records"
    __table_args__ = (
        sa.CheckConstraint(
            "purpose IN ('chat', 'learning_quiz', 'learning_grade', 'query_rewrite', "
            "'embedding', 'classification')",
            name="ck_model_call_records_purpose_values",
        ),
        sa.CheckConstraint("elapsed_ms IS NULL OR elapsed_ms >= 0", name="ck_model_call_records_elapsed"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["study.users.id"],
            name="fk_model_call_records_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_model_call_records_owner_created",
            "owner_user_id",
            sa.text("created_at DESC"),
            unique=False,
        ),
        {"schema": "study"},
    )

    id = sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        nullable=False,
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    owner_user_id = sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False)
    purpose = sa.Column("purpose", sa.String(32), nullable=False)
    model_name = sa.Column("model_name", sa.String(120), nullable=False)
    used_fallback = sa.Column("used_fallback", sa.Boolean(), nullable=False, server_default=sa.text("false"))
    usage = sa.Column("usage", postgresql.JSONB(), nullable=True)
    elapsed_ms = sa.Column("elapsed_ms", sa.Numeric(12, 3), nullable=True)
    success = sa.Column("success", sa.Boolean(), nullable=False)
    created_at = sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )