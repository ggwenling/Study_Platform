"""Add Google identities and single-use password resets."""

import sqlalchemy as sa

from alembic import op

revision = "0003_bearer_auth"
down_revision = "0002_personal_rag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "google_identities",
        sa.Column("subject", sa.String(255), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("study.users.id", name="fk_google_identities_user_id", ondelete="RESTRICT"), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_google_identities_user_id"),
        schema="study",
    )
    op.create_table(
        "password_resets",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("study.users.id", name="fk_password_resets_user_id", ondelete="RESTRICT")),
        sa.Column("email_hash", sa.String(64), nullable=False),
        sa.Column("client_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        schema="study",
    )
    op.create_index("ix_password_resets_email_time", "password_resets", ["email_hash", "created_at"], schema="study")
    op.create_index("ix_password_resets_client_time", "password_resets", ["client_hash", "created_at"], schema="study")
    op.execute("UPDATE study.user_sessions SET revoked_at = now() WHERE revoked_at IS NULL")


def downgrade() -> None:
    op.drop_table("password_resets", schema="study")
    op.drop_table("google_identities", schema="study")
    op.execute("UPDATE study.user_sessions SET revoked_at = now() WHERE revoked_at IS NULL")
