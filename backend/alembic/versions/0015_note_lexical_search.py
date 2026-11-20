"""Add a lexical index to research notes."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from study_platform.services.lexical import lexical_index_text

revision = "0015_note_lexical_search"
down_revision = "0014_vision_model_connections"
branch_labels = None
depends_on = None

_BATCH = 500


def upgrade() -> None:
    op.add_column(
        "notes",
        sa.Column("lexical", postgresql.TSVECTOR(), nullable=True),
        schema="study",
        if_not_exists=True,
    )
    if not op.get_context().opts.get("as_sql"):
        _backfill()
    op.create_index(
        "ix_notes_lexical", "notes", ["lexical"], schema="study", postgresql_using="gin",
        if_not_exists=True,
    )


def _backfill() -> None:
    query = sa.text("""
        SELECT id, title, content_md
        FROM study.notes
        WHERE lexical IS NULL AND deleted_at IS NULL
        ORDER BY id LIMIT :limit
    """)
    update = sa.text("""
        UPDATE study.notes AS note
        SET lexical = to_tsvector('simple', batch.indexed)
        FROM unnest(:ids, :terms) AS batch(id, indexed)
        WHERE note.id = batch.id AND note.lexical IS NULL AND note.deleted_at IS NULL
    """).bindparams(
        sa.bindparam("ids", type_=postgresql.ARRAY(sa.Uuid())),
        sa.bindparam("terms", type_=postgresql.ARRAY(sa.Text())),
    )
    # Publish the nullable column first and commit each bounded UPDATE independently.
    # A retry can resume by reading only rows that still lack their lexical value.
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        while True:
            rows = bind.execute(query, {"limit": _BATCH}).all()
            if not rows:
                break
            bind.execute(update, {
                "ids": [row.id for row in rows],
                "terms": [lexical_index_text(f"{row.title}\n{row.content_md}") for row in rows],
            })


def downgrade() -> None:
    op.drop_index("ix_notes_lexical", table_name="notes", schema="study")
    op.drop_column("notes", "lexical", schema="study")
