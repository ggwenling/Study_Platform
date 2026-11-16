"""Align knowledge-base ids with legacy course FK anchors (conservative, reversible)."""

from sqlalchemy.orm import Session

from study_platform.persistence.models import Course, KnowledgeBase


def ensure_course_anchor(db: Session, kb: KnowledgeBase) -> Course:
    """Upsert a courses row with the same id as the knowledge base for legacy FKs."""
    row = db.get(Course, kb.id)
    if row is not None:
        if row.owner_user_id != kb.owner_user_id:
            raise ValueError("knowledge base owner mismatch")
        return row
    row = Course(
        id=kb.id,
        owner_user_id=kb.owner_user_id,
        name=kb.name[:200],
        description=kb.description,
    )
    db.add(row)
    db.flush()
    return row