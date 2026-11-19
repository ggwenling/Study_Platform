"""Add reversible conversation archiving."""

import sqlalchemy as sa

from alembic import op

revision = "0011_conversation_archive"
down_revision = "0010_model_connections"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("conversations", sa.Column("archived_at", sa.DateTime(timezone=True)), schema="study")


def downgrade():
    op.drop_column("conversations", "archived_at", schema="study")
