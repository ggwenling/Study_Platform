"""User model connections, encrypted revisions and library bindings."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0010_model_connections"
down_revision = "0009_model_call_records"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    op.create_table(
        "model_connections",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "owner_user_id",
            sa.Uuid(),
            sa.ForeignKey("study.users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
        sa.Column("versions", JSONB, nullable=False),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("tested_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(80)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("kind IN ('llm', 'embedding')", name="ck_model_connections_kind"),
        schema="study",
    )
    op.create_index(
        "uq_model_connections_default",
        "model_connections",
        ["owner_user_id", "kind"],
        unique=True,
        postgresql_where=sa.text("is_default AND deleted_at IS NULL"),
        schema="study",
    )
    op.create_table(
        "model_connection_leases",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("study.model_connections.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        schema="study",
    )
    op.add_column(
        "knowledge_bases",
        sa.Column(
            "embedding_connection_id",
            sa.Uuid(),
            sa.ForeignKey("study.model_connections.id", ondelete="RESTRICT"),
        ),
        schema="study",
    )
    op.add_column("knowledge_bases", sa.Column("embedding_binding", JSONB), schema="study")
    # Pin the platform model/endpoint for libraries that predate personal connections.
    from study_platform.core.config import get_settings

    settings = get_settings()
    binding = {
        "base_url": settings.embedding_base_url.rstrip("/"),
        "model": settings.embedding_model,
        "dimensions": 1024,
        "profile": f"{settings.embedding_model}:1024",
    }
    bind.execute(
        sa.text("UPDATE study.knowledge_bases SET embedding_binding=:binding").bindparams(
            sa.bindparam("binding", type_=JSONB)
        ),
        {"binding": binding},
    )
    bind.execute(
        sa.text("""
        UPDATE study.knowledge_bases kb
        SET embedding_binding = kb.embedding_binding || jsonb_build_object(
            'profile', profiles.profile, 'model', regexp_replace(profiles.profile, chr(58) || '1024$', ''))
        FROM (
            SELECT DISTINCT ON (m.knowledge_base_id) m.knowledge_base_id, i.embedding_profile AS profile
            FROM study.materials m
            JOIN study.material_versions v ON v.material_id=m.id AND v.is_current
            JOIN study.index_versions i ON i.material_version_id=v.id AND i.is_active AND i.status='ready'
            ORDER BY m.knowledge_base_id, i.ready_at DESC NULLS LAST
        ) profiles
        WHERE profiles.knowledge_base_id=kb.id
    """)
    )
    op.add_column(
        "model_call_records",
        sa.Column("connection_source", sa.String(200), nullable=False, server_default="platform"),
        schema="study",
    )
    op.drop_constraint("ck_model_call_records_purpose_values", "model_call_records", schema="study")
    op.create_check_constraint(
        "ck_model_call_records_purpose_values",
        "model_call_records",
        "purpose IN ('chat', 'learning_quiz', 'learning_grade', 'query_rewrite', 'embedding', 'classification', 'connection_test')",
        schema="study",
    )


def downgrade():
    op.execute("UPDATE study.model_call_records SET purpose='chat' WHERE purpose='connection_test'")
    op.drop_constraint("ck_model_call_records_purpose_values", "model_call_records", schema="study")
    op.create_check_constraint(
        "ck_model_call_records_purpose_values",
        "model_call_records",
        "purpose IN ('chat', 'learning_quiz', 'learning_grade', 'query_rewrite', 'embedding', 'classification')",
        schema="study",
    )
    op.drop_column("model_call_records", "connection_source", schema="study")
    op.drop_column("knowledge_bases", "embedding_connection_id", schema="study")
    op.drop_column("knowledge_bases", "embedding_binding", schema="study")
    op.drop_table("model_connection_leases", schema="study")
    op.drop_table("model_connections", schema="study")
