"""Persist source URLs for clipped webpages."""

import sqlalchemy as sa

from alembic import op

revision = "0013_material_source_url"
down_revision = "0012_citation_quote_snapshots"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("materials", sa.Column("source_url", sa.Text(), nullable=True), schema="study")


def downgrade():
    op.drop_column("materials", "source_url", schema="study")
