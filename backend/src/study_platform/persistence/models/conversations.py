"""Conversations database models; constraints mirror the initial migration."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from study_platform.persistence.base import Base


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["project_knowledge_base_id", "owner_user_id"],
            ["study.knowledge_bases.id", "study.knowledge_bases.owner_user_id"],
            name="fk_conversations_project_owner", ondelete="RESTRICT", use_alter=True,
        ),
        sa.Index("ix_conversations_owner_project_created", "owner_user_id", "project_knowledge_base_id", sa.text("created_at DESC")),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_conversations_id_owner_user_id"),
        sa.CheckConstraint(
            "jsonb_typeof(scope_snapshot) = 'array'", name="ck_conversations_scope_snapshot_json"
        ),
        sa.CheckConstraint(
            "mode IN ('normal', 'explain', 'socratic', 'exam')", name="ck_conversations_mode_values"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["study.users.id"],
            name="fk_conversations_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "owner_user_id"],
            ["study.courses.id", "study.courses.owner_user_id"],
            name="fk_conversations_course_id_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_conversations_owner_created",
            "owner_user_id",
            sa.text("created_at DESC"),
            unique=False,
        ),
        sa.Index(
            "ix_conversations_owner_course_updated",
            "owner_user_id",
            "course_id",
            sa.text("updated_at DESC"),
            unique=False,
        ),
        {"schema": "study"},
    )

    id = sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        nullable=False,
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    owner_user_id = sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False)
    course_id = sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=True)
    project_knowledge_base_id = sa.Column(postgresql.UUID(as_uuid=True), nullable=True)
    title = sa.Column("title", sa.String(200), nullable=True)
    mode = sa.Column("mode", sa.String(16), nullable=False, server_default=sa.text("'normal'"))
    scope_snapshot = sa.Column(
        "scope_snapshot", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    created_at = sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    updated_at = sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
        onupdate=sa.func.now(),
    )
    deleted_at = sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    archived_at = sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        sa.UniqueConstraint(
            "conversation_id", "sequence_no", name="uq_messages_conversation_id_sequence_no"
        ),
        sa.CheckConstraint("sequence_no >= 0", name="ck_messages_sequence_no_nonnegative"),
        sa.CheckConstraint("jsonb_typeof(usage) = 'object'", name="ck_messages_usage_json"),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system_summary')", name="ck_messages_role_values"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'streaming', 'completed', 'failed', 'cancelled')",
            name="ck_messages_status_values",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["study.conversations.id"],
            name="fk_messages_conversation_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id", "conversation_id"],
            ["study.ai_workflow_runs.id", "study.ai_workflow_runs.conversation_id"],
            name="fk_messages_workflow_run_id_conversation_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index("ix_messages_conversation_created", "conversation_id", "created_at", unique=False),
        sa.Index("ix_messages_user_run", "workflow_run_id", unique=True,
                 postgresql_where=sa.text("role = 'user' AND workflow_run_id IS NOT NULL")),
        sa.Index("ix_messages_workflow_run", "workflow_run_id", unique=False),
        sa.Index(
            "ix_messages_assistant_run",
            "workflow_run_id",
            unique=True,
            postgresql_where=sa.text("role = 'assistant' AND workflow_run_id IS NOT NULL"),
        ),
        {"schema": "study"},
    )

    id = sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        nullable=False,
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    conversation_id = sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False)
    role = sa.Column("role", sa.String(16), nullable=False)
    content = sa.Column("content", sa.Text(), nullable=False)
    status = sa.Column("status", sa.String(16), nullable=False)
    sequence_no = sa.Column("sequence_no", sa.BigInteger(), nullable=False)
    model_profile = sa.Column("model_profile", sa.String(128), nullable=True)
    prompt_version = sa.Column("prompt_version", sa.String(50), nullable=True)
    workflow_run_id = sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), nullable=True)
    usage = sa.Column("usage", postgresql.JSONB(), nullable=True)
    created_at = sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    updated_at = sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
        onupdate=sa.func.now(),
    )


class MessageCitation(Base):
    __tablename__ = "message_citations"
    __table_args__ = (
        sa.UniqueConstraint(
            "message_id", "ordinal", name="uq_message_citations_message_id_ordinal"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(locator) = 'object'", name="ck_message_citations_locator_json"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_message_citations_ordinal_nonnegative"),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["study.messages.id"],
            name="fk_message_citations_message_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id", "index_version_id", "material_version_id"],
            [
                "study.material_chunks.id",
                "study.material_chunks.index_version_id",
                "study.material_chunks.material_version_id",
            ],
            name="fk_message_citations_chunk_id_index_version_id_materia_e555d84b",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        {"schema": "study"},
    )

    id = sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        nullable=False,
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    message_id = sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False)
    chunk_id = sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False)
    material_version_id = sa.Column(
        "material_version_id", postgresql.UUID(as_uuid=True), nullable=False
    )
    index_version_id = sa.Column("index_version_id", postgresql.UUID(as_uuid=True), nullable=False)
    locator = sa.Column("locator", postgresql.JSONB(), nullable=False)
    quote_sha256 = sa.Column("quote_sha256", sa.CHAR(64), nullable=False)
    quote_text = sa.Column("quote_text", sa.Text(), nullable=True)
    ordinal = sa.Column("ordinal", sa.SmallInteger(), nullable=False)
    created_at = sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


class Note(Base):
    __tablename__ = "notes"
    __table_args__ = (
        sa.CheckConstraint("edit_revision >= 0", name="ck_notes_edit_revision_nonnegative"),
        sa.CheckConstraint(
            "jsonb_typeof(source_scope) IN ('object', 'array')", name="ck_notes_source_scope_json"
        ),
        sa.CheckConstraint(
            "note_type IN ('summary', 'chapter_summary', 'formula_sheet', 'exam_guide')",
            name="ck_notes_note_type_values",
        ),
        sa.CheckConstraint("status IN ('draft', 'ready', 'failed')", name="ck_notes_status_values"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["study.users.id"],
            name="fk_notes_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "owner_user_id"],
            ["study.courses.id", "study.courses.owner_user_id"],
            name="fk_notes_course_id_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_notes_owner_created", "owner_user_id", sa.text("created_at DESC"), unique=False
        ),
        sa.Index(
            "ix_notes_owner_course_created",
            "owner_user_id",
            "course_id",
            sa.text("created_at DESC"),
            unique=False,
        ),
        {"schema": "study"},
    )

    id = sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        nullable=False,
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    owner_user_id = sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False)
    course_id = sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False)
    title = sa.Column("title", sa.String(255), nullable=False)
    note_type = sa.Column("note_type", sa.String(24), nullable=False)
    content_md = sa.Column("content_md", sa.Text(), nullable=False)
    generated_content_md = sa.Column("generated_content_md", sa.Text(), nullable=False)
    edit_revision = sa.Column(
        "edit_revision", sa.Integer(), nullable=False, server_default=sa.text("0")
    )
    edited_at = sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True)
    status = sa.Column("status", sa.String(16), nullable=False)
    source_scope = sa.Column("source_scope", postgresql.JSONB(), nullable=False)
    generator_profile = sa.Column("generator_profile", sa.String(128), nullable=False)
    prompt_version = sa.Column("prompt_version", sa.String(50), nullable=False)
    created_at = sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
    updated_at = sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
        onupdate=sa.func.now(),
    )
    deleted_at = sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)


class NoteCitation(Base):
    __tablename__ = "note_citations"
    __table_args__ = (
        sa.UniqueConstraint("note_id", "ordinal", name="uq_note_citations_note_id_ordinal"),
        sa.CheckConstraint(
            "jsonb_typeof(locator) = 'object'", name="ck_note_citations_locator_json"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_note_citations_ordinal_nonnegative"),
        sa.ForeignKeyConstraint(
            ["note_id"],
            ["study.notes.id"],
            name="fk_note_citations_note_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id", "index_version_id", "material_version_id"],
            [
                "study.material_chunks.id",
                "study.material_chunks.index_version_id",
                "study.material_chunks.material_version_id",
            ],
            name="fk_note_citations_chunk_id_index_version_id_material_version_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        {"schema": "study"},
    )

    id = sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        nullable=False,
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    note_id = sa.Column("note_id", postgresql.UUID(as_uuid=True), nullable=False)
    chunk_id = sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False)
    material_version_id = sa.Column(
        "material_version_id", postgresql.UUID(as_uuid=True), nullable=False
    )
    index_version_id = sa.Column("index_version_id", postgresql.UUID(as_uuid=True), nullable=False)
    locator = sa.Column("locator", postgresql.JSONB(), nullable=False)
    quote_sha256 = sa.Column("quote_sha256", sa.CHAR(64), nullable=True)
    quote_text = sa.Column("quote_text", sa.Text(), nullable=True)
    ordinal = sa.Column("ordinal", sa.Integer(), nullable=False)
    created_at = sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
