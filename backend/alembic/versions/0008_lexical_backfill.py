"""Backfill material_chunks.lexical with jieba simple tsvector tokens."""

import sqlalchemy as sa
from sqlalchemy.orm import Session

from alembic import op
from study_platform.services.lexical import lexical_index_text

revision = "0008_lexical_backfill"
down_revision = "0007_learning_verdict"
branch_labels = None
depends_on = None

_BATCH = 500


def upgrade() -> None:
    if op.get_context().opts.get("as_sql"):
        return
    bind = op.get_bind()
    session = Session(bind=bind)
    try:
        while True:
            rows = session.execute(
                sa.text(
                    """
                    SELECT id, text
                    FROM study.material_chunks
                    WHERE lexical IS NULL
                    ORDER BY created_at, id
                    LIMIT :limit
                    """
                ),
                {"limit": _BATCH},
            ).all()
            if not rows:
                break
            for chunk_id, text in rows:
                indexed = lexical_index_text(text or "")
                session.execute(
                    sa.text(
                        """
                        UPDATE study.material_chunks
                        SET lexical = to_tsvector('simple', :indexed)
                        WHERE id = :id
                        """
                    ),
                    {"id": chunk_id, "indexed": indexed},
                )
            session.commit()
    finally:
        session.close()


def downgrade() -> None:
    op.execute(sa.text("UPDATE study.material_chunks SET lexical = NULL"))