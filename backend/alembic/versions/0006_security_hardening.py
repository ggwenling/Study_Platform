"""Session access generation for JWT invalidation on refresh."""

import sqlalchemy as sa

from alembic import op

revision = "0006_security_hardening"
down_revision = "0005_library_detail"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_sessions",
        sa.Column("access_generation", sa.Integer(), nullable=False, server_default="0"),
        schema="study",
    )
    op.alter_column("user_sessions", "access_generation", server_default=None, schema="study")


def downgrade() -> None:
    op.drop_column("user_sessions", "access_generation", schema="study")