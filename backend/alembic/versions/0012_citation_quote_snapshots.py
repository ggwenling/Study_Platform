"""Preserve the text used by historical citations."""

import sqlalchemy as sa

from alembic import op

revision = "0012_citation_quote_snapshots"
down_revision = "0011_conversation_archive"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("message_citations", "note_citations", "quiz_item_sources"):
        op.add_column(table, sa.Column("quote_text", sa.Text()), schema="study")
    op.add_column("note_citations", sa.Column("quote_sha256", sa.CHAR(64)), schema="study")

    op.execute("""
        UPDATE study.message_citations AS citation
        SET quote_text = chunk.text
        FROM study.material_chunks AS chunk
        WHERE chunk.id = citation.chunk_id
          AND citation.quote_sha256 = encode(sha256(convert_to(chunk.text, 'UTF8')), 'hex')
    """)
    op.execute("""
        UPDATE study.note_citations AS citation
        SET quote_text = original.quote_text, quote_sha256 = original.quote_sha256
        FROM study.notes AS note, study.message_citations AS original
        WHERE note.id = citation.note_id
          AND note.source_scope->>'message_id' = CAST(original.message_id AS text)
          AND original.ordinal = citation.ordinal
          AND original.chunk_id = citation.chunk_id
    """)


def downgrade():
    op.drop_column("note_citations", "quote_sha256", schema="study")
    for table in ("quiz_item_sources", "note_citations", "message_citations"):
        op.drop_column(table, "quote_text", schema="study")
