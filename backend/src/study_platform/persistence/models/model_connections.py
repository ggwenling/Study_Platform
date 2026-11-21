"""Account model connections and immutable credential revisions."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from study_platform.persistence.base import Base


class ModelConnection(Base):
    __tablename__ = "model_connections"
    __table_args__ = (
        sa.CheckConstraint("kind IN ('llm', 'embedding', 'vision')", name="ck_model_connections_kind"),
        sa.Index(
            "uq_model_connections_default",
            "owner_user_id",
            "kind",
            unique=True,
            postgresql_where=sa.text("is_default AND deleted_at IS NULL"),
        ),
        {"schema": "study"},
    )
    id = sa.Column(
        UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    owner_user_id = sa.Column(
        UUID(as_uuid=True), sa.ForeignKey("study.users.id", ondelete="RESTRICT"), nullable=False
    )
    kind = sa.Column(sa.String(16), nullable=False)
    name = sa.Column(sa.String(80), nullable=False)
    revision = sa.Column(sa.Integer, nullable=False, server_default="1")
    versions = sa.Column(JSONB, nullable=False)
    is_default = sa.Column(sa.Boolean, nullable=False, server_default=sa.false())
    status = sa.Column(sa.String(16), nullable=False, server_default="draft")
    tested_at = sa.Column(sa.DateTime(timezone=True))
    error_code = sa.Column(sa.String(80))
    created_at = sa.Column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    updated_at = sa.Column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
        onupdate=sa.func.now(),
    )
    deleted_at = sa.Column(sa.DateTime(timezone=True))


class ModelConnectionLease(Base):
    __tablename__ = "model_connection_leases"
    __table_args__ = ({"schema": "study"},)
    id = sa.Column(
        UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    connection_id = sa.Column(
        UUID(as_uuid=True),
        sa.ForeignKey("study.model_connections.id", ondelete="RESTRICT"),
        nullable=False,
    )
    expires_at = sa.Column(sa.DateTime(timezone=True), nullable=False)
