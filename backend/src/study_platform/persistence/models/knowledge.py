"""Personal knowledge bases, independently owned from legacy courses."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from study_platform.persistence.base import Base


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        sa.UniqueConstraint("id", "owner_user_id", name="uq_knowledge_bases_id_owner_user_id"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["study.users.id"],
            name="fk_knowledge_bases_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        {"schema": "study"},
    )
    id = sa.Column(
        postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    owner_user_id = sa.Column(postgresql.UUID(as_uuid=True), nullable=False)
    name = sa.Column(sa.String(200), nullable=False)
    description = sa.Column(sa.Text(), nullable=True)
    created_at = sa.Column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    updated_at = sa.Column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
        onupdate=sa.func.now(),
    )
    embedding_connection_id = sa.Column(postgresql.UUID(as_uuid=True), sa.ForeignKey("study.model_connections.id", ondelete="RESTRICT"), nullable=True)
    embedding_binding = sa.Column(postgresql.JSONB(), nullable=True)
    last_synced_at = sa.Column(sa.DateTime(timezone=True), nullable=True)
    deleted_at = sa.Column(sa.DateTime(timezone=True), nullable=True)
