"""Create the 37 study business tables and dedicated graph schema.

Revision ID: 0001_initial_schema
"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA IF NOT EXISTS study")
    op.execute("CREATE SCHEMA IF NOT EXISTS study_graph")
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default=sa.text("'student'")),
        sa.Column("status", sa.String(24), nullable=False, server_default=sa.text("'active'")),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint("role IN ('student', 'admin')", name="ck_users_role_values"),
        sa.CheckConstraint(
            "status IN ('active', 'disabled', 'pending_verification')",
            name="ck_users_status_values",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        schema="study",
    )
    op.create_table(
        "user_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("refresh_token_hash", sa.CHAR(64), nullable=False),
        sa.Column("csrf_secret_hash", sa.CHAR(64), nullable=False),
        sa.Column("user_agent_hash", sa.CHAR(64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("refresh_token_hash", name="uq_user_sessions_refresh_token_hash"),
        sa.PrimaryKeyConstraint("id", name="pk_user_sessions"),
        schema="study",
    )
    op.create_table(
        "courses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("subject", sa.String(80), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("language", sa.String(16), nullable=False, server_default=sa.text("'zh-CN'")),
        sa.Column(
            "settings", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_courses_id_owner_user_id"),
        sa.CheckConstraint("jsonb_typeof(settings) = 'object'", name="ck_courses_settings_json"),
        sa.PrimaryKeyConstraint("id", name="pk_courses"),
        schema="study",
    )
    op.create_table(
        "materials",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'active'")),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_materials_id_owner_user_id"),
        sa.CheckConstraint(
            "kind IN ('notes', 'textbook', 'exam', 'worksheet', 'other')",
            name="ck_materials_kind_values",
        ),
        sa.CheckConstraint("status IN ('active', 'deleted')", name="ck_materials_status_values"),
        sa.PrimaryKeyConstraint("id", name="pk_materials"),
        schema="study",
    )
    op.create_table(
        "material_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("material_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source_mime", sa.String(100), nullable=False),
        sa.Column("source_sha256", sa.CHAR(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("parser_profile", sa.String(120), nullable=True),
        sa.Column(
            "parse_status", sa.String(20), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("failure_code", sa.String(64), nullable=True),
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
        sa.UniqueConstraint("object_key", name="uq_material_versions_object_key"),
        sa.UniqueConstraint(
            "material_id", "version_no", name="uq_material_versions_material_id_version_no"
        ),
        sa.CheckConstraint("version_no > 0", name="ck_material_versions_version_no_positive"),
        sa.CheckConstraint("byte_size > 0", name="ck_material_versions_byte_size_positive"),
        sa.CheckConstraint("page_count > 0", name="ck_material_versions_page_count_positive"),
        sa.CheckConstraint(
            "parse_status IN ('pending', 'processing', 'ready', 'failed', 'needs_ocr')",
            name="ck_material_versions_parse_status_values",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_material_versions"),
        schema="study",
    )
    op.create_table(
        "material_sections",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("material_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_section_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("heading", sa.Text(), nullable=True),
        sa.Column("level", sa.SmallInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("locator", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "id", "material_version_id", name="uq_material_sections_id_material_version_id"
        ),
        sa.UniqueConstraint(
            "material_version_id",
            "ordinal",
            name="uq_material_sections_material_version_id_ordinal",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_material_sections_ordinal_nonnegative"),
        sa.CheckConstraint(
            "jsonb_typeof(locator) = 'object'", name="ck_material_sections_locator_json"
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1", name="ck_material_sections_confidence_range"
        ),
        sa.CheckConstraint("level BETWEEN 1 AND 8", name="ck_material_sections_level_range"),
        sa.CheckConstraint(
            "parent_section_id IS NULL OR parent_section_id <> id",
            name="ck_material_sections_not_self_parent",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_material_sections"),
        schema="study",
    )
    op.create_table(
        "index_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("material_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("pipeline_profile", sa.String(128), nullable=False),
        sa.Column("embedding_profile", sa.String(128), nullable=False),
        sa.Column(
            "embedding_dim", sa.SmallInteger(), nullable=False, server_default=sa.text("1024")
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("manifest_sha256", sa.CHAR(64), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "id", "material_version_id", name="uq_index_versions_id_material_version_id"
        ),
        sa.UniqueConstraint(
            "material_version_id",
            "generation",
            name="uq_index_versions_material_version_id_generation",
        ),
        sa.CheckConstraint("generation > 0", name="ck_index_versions_generation_positive"),
        sa.CheckConstraint("chunk_count >= 0", name="ck_index_versions_chunk_count_nonnegative"),
        sa.CheckConstraint(
            "status IN ('building', 'ready', 'failed', 'retired')",
            name="ck_index_versions_status_values",
        ),
        sa.CheckConstraint("embedding_dim = 1024", name="ck_index_versions_embedding_dim_fixed"),
        sa.CheckConstraint(
            "NOT is_active OR (status = 'ready' AND ready_at IS NOT NULL)",
            name="ck_index_versions_active_ready",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_index_versions"),
        schema="study",
    )
    op.create_table(
        "material_chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("index_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("material_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.CHAR(64), nullable=False),
        sa.Column("locator", postgresql.JSONB(), nullable=False),
        sa.Column("lexical", postgresql.TSVECTOR(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "id",
            "index_version_id",
            "material_version_id",
            name="uq_material_chunks_id_index_version_id_material_version_id",
        ),
        sa.UniqueConstraint(
            "index_version_id", "ordinal", name="uq_material_chunks_index_version_id_ordinal"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_material_chunks_ordinal_nonnegative"),
        sa.CheckConstraint(
            "jsonb_typeof(locator) = 'object'", name="ck_material_chunks_locator_json"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_material_chunks"),
        schema="study",
    )
    op.create_table(
        "chunk_embeddings",
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("model_profile_id", sa.String(128), nullable=False, primary_key=True),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("embedding_sha256", sa.CHAR(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("chunk_id", "model_profile_id", name="pk_chunk_embeddings"),
        schema="study",
    )
    op.create_table(
        "knowledge_points",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("canonical_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("subject_tag", sa.String(80), nullable=True),
        sa.Column("difficulty", sa.SmallInteger(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
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
        sa.UniqueConstraint("id", "course_id", name="uq_knowledge_points_id_course_id"),
        sa.UniqueConstraint(
            "course_id",
            "parent_id",
            "canonical_name",
            name="uq_knowledge_points_course_id_parent_id_canonical_name",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint(
            "difficulty BETWEEN 1 AND 5", name="ck_knowledge_points_difficulty_range"
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1", name="ck_knowledge_points_confidence_range"
        ),
        sa.CheckConstraint("source IN ('ai', 'manual')", name="ck_knowledge_points_source_values"),
        sa.CheckConstraint(
            "status IN ('draft', 'verified', 'archived')", name="ck_knowledge_points_status_values"
        ),
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id", name="ck_knowledge_points_not_self_parent"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_points"),
        schema="study",
    )
    op.create_table(
        "knowledge_point_sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("knowledge_point_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("material_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("index_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_span", postgresql.JSONB(), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "knowledge_point_id",
            "chunk_id",
            name="uq_knowledge_point_sources_knowledge_point_id_chunk_id",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_span) = 'object'",
            name="ck_knowledge_point_sources_evidence_span_json",
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1", name="ck_knowledge_point_sources_confidence_range"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_point_sources"),
        schema="study",
    )
    op.create_table(
        "source_questions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("material_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("question_type", sa.String(24), nullable=False),
        sa.Column("stem", sa.Text(), nullable=False),
        sa.Column("options", postgresql.JSONB(), nullable=True),
        sa.Column("reference_answer", postgresql.JSONB(), nullable=True),
        sa.Column("solution", sa.Text(), nullable=True),
        sa.Column("locator", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "material_version_id", "ordinal", name="uq_source_questions_material_version_id_ordinal"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_source_questions_ordinal_nonnegative"),
        sa.CheckConstraint(
            "jsonb_typeof(options) = 'array'", name="ck_source_questions_options_json"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(reference_answer) IN ('object', 'array', 'string', 'number', 'boolean')",
            name="ck_source_questions_reference_answer_json",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(locator) = 'object'", name="ck_source_questions_locator_json"
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1", name="ck_source_questions_confidence_range"
        ),
        sa.CheckConstraint(
            "question_type IN ('mcq', 'true_false', 'fill_blank', 'short_answer', 'other')",
            name="ck_source_questions_question_type_values",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_questions"),
        schema="study",
    )
    op.create_table(
        "conversations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("mode", sa.String(16), nullable=False, server_default=sa.text("'normal'")),
        sa.Column(
            "scope_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_conversations_id_owner_user_id"),
        sa.CheckConstraint(
            "jsonb_typeof(scope_snapshot) = 'array'", name="ck_conversations_scope_snapshot_json"
        ),
        sa.CheckConstraint(
            "mode IN ('normal', 'explain', 'socratic', 'exam')", name="ck_conversations_mode_values"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
        schema="study",
    )
    op.create_table(
        "messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("model_profile", sa.String(128), nullable=True),
        sa.Column("prompt_version", sa.String(50), nullable=True),
        sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("usage", postgresql.JSONB(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        schema="study",
    )
    op.create_table(
        "message_citations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("material_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("index_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("locator", postgresql.JSONB(), nullable=False),
        sa.Column("quote_sha256", sa.CHAR(64), nullable=False),
        sa.Column("ordinal", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "message_id", "ordinal", name="uq_message_citations_message_id_ordinal"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(locator) = 'object'", name="ck_message_citations_locator_json"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_message_citations_ordinal_nonnegative"),
        sa.PrimaryKeyConstraint("id", name="pk_message_citations"),
        schema="study",
    )
    op.create_table(
        "notes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("note_type", sa.String(24), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("generated_content_md", sa.Text(), nullable=False),
        sa.Column("edit_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source_scope", postgresql.JSONB(), nullable=False),
        sa.Column("generator_profile", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("edit_revision >= 0", name="ck_notes_edit_revision_nonnegative"),
        sa.CheckConstraint(
            "jsonb_typeof(source_scope) IN ('object', 'array')", name="ck_notes_source_scope_json"
        ),
        sa.CheckConstraint(
            "note_type IN ('summary', 'chapter_summary', 'formula_sheet', 'exam_guide')",
            name="ck_notes_note_type_values",
        ),
        sa.CheckConstraint("status IN ('draft', 'ready', 'failed')", name="ck_notes_status_values"),
        sa.PrimaryKeyConstraint("id", name="pk_notes"),
        schema="study",
    )
    op.create_table(
        "note_citations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("note_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("material_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("index_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("locator", postgresql.JSONB(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("note_id", "ordinal", name="uq_note_citations_note_id_ordinal"),
        sa.CheckConstraint(
            "jsonb_typeof(locator) = 'object'", name="ck_note_citations_locator_json"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_note_citations_ordinal_nonnegative"),
        sa.PrimaryKeyConstraint("id", name="pk_note_citations"),
        schema="study",
    )
    op.create_table(
        "quizzes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("difficulty", sa.SmallInteger(), nullable=True),
        sa.Column("source_scope", postgresql.JSONB(), nullable=False),
        sa.Column("generator_profile", sa.String(128), nullable=True),
        sa.Column("prompt_version", sa.String(50), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_quizzes_id_owner_user_id"),
        sa.CheckConstraint("difficulty BETWEEN 1 AND 5", name="ck_quizzes_difficulty_range"),
        sa.CheckConstraint(
            "jsonb_typeof(source_scope) IN ('object', 'array')", name="ck_quizzes_source_scope_json"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'ready', 'archived')", name="ck_quizzes_status_values"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quizzes"),
        schema="study",
    )
    op.create_table(
        "quiz_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("quiz_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_point_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("item_type", sa.String(24), nullable=False),
        sa.Column("stem", sa.Text(), nullable=False),
        sa.Column("options", postgresql.JSONB(), nullable=True),
        sa.Column("answer_key", postgresql.JSONB(), nullable=False),
        sa.Column("rubric", postgresql.JSONB(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("difficulty", sa.SmallInteger(), nullable=True),
        sa.Column("max_score", sa.Numeric(8, 2), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("id", "quiz_id", name="uq_quiz_items_id_quiz_id"),
        sa.UniqueConstraint("quiz_id", "ordinal", name="uq_quiz_items_quiz_id_ordinal"),
        sa.CheckConstraint("ordinal >= 0", name="ck_quiz_items_ordinal_nonnegative"),
        sa.CheckConstraint("jsonb_typeof(options) = 'array'", name="ck_quiz_items_options_json"),
        sa.CheckConstraint(
            "jsonb_typeof(answer_key) IN ('object', 'array', 'string', 'number', 'boolean')",
            name="ck_quiz_items_answer_key_json",
        ),
        sa.CheckConstraint("jsonb_typeof(rubric) = 'object'", name="ck_quiz_items_rubric_json"),
        sa.CheckConstraint("difficulty BETWEEN 1 AND 5", name="ck_quiz_items_difficulty_range"),
        sa.CheckConstraint("max_score > 0", name="ck_quiz_items_max_score_positive"),
        sa.CheckConstraint(
            "item_type IN ('mcq', 'true_false', 'fill_blank', 'short_answer')",
            name="ck_quiz_items_item_type_values",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quiz_items"),
        schema="study",
    )
    op.create_table(
        "quiz_item_sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("quiz_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("material_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("index_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("locator", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "quiz_item_id", "chunk_id", name="uq_quiz_item_sources_quiz_item_id_chunk_id"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(locator) = 'object'", name="ck_quiz_item_sources_locator_json"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quiz_item_sources"),
        schema="study",
    )
    op.create_table(
        "quiz_attempts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("quiz_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("graded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_score", sa.Numeric(10, 2), nullable=True),
        sa.Column("max_score", sa.Numeric(10, 2), nullable=False),
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
        sa.UniqueConstraint("id", "quiz_id", name="uq_quiz_attempts_id_quiz_id"),
        sa.CheckConstraint("total_score >= 0", name="ck_quiz_attempts_total_score_nonnegative"),
        sa.CheckConstraint("max_score > 0", name="ck_quiz_attempts_max_score_positive"),
        sa.CheckConstraint(
            "status IN ('in_progress', 'submitted', 'graded')",
            name="ck_quiz_attempts_status_values",
        ),
        sa.CheckConstraint("total_score <= max_score", name="ck_quiz_attempts_total_score_range"),
        sa.PrimaryKeyConstraint("id", name="pk_quiz_attempts"),
        schema="study",
    )
    op.create_table(
        "quiz_responses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quiz_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quiz_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("answer", postgresql.JSONB(), nullable=False),
        sa.Column("score", sa.Numeric(8, 2), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("grader_profile", sa.String(128), nullable=True),
        sa.Column("graded_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("id", "quiz_item_id", name="uq_quiz_responses_id_quiz_item_id"),
        sa.UniqueConstraint(
            "attempt_id", "quiz_item_id", name="uq_quiz_responses_attempt_id_quiz_item_id"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(answer) IN ('object', 'array', 'string', 'number', 'boolean')",
            name="ck_quiz_responses_answer_json",
        ),
        sa.CheckConstraint("score >= 0", name="ck_quiz_responses_score_nonnegative"),
        sa.PrimaryKeyConstraint("id", name="pk_quiz_responses"),
        schema="study",
    )
    op.create_table(
        "wrong_questions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quiz_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_response_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("wrong_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("last_wrong_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "owner_user_id", "quiz_item_id", name="uq_wrong_questions_owner_user_id_quiz_item_id"
        ),
        sa.CheckConstraint("wrong_count >= 0", name="ck_wrong_questions_wrong_count_nonnegative"),
        sa.CheckConstraint(
            "status IN ('open', 'reviewing', 'mastered')", name="ck_wrong_questions_status_values"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_wrong_questions"),
        schema="study",
    )
    op.create_table(
        "flashcard_decks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("source_scope", postgresql.JSONB(), nullable=False),
        sa.Column("generator_profile", sa.String(128), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "jsonb_typeof(source_scope) IN ('object', 'array')",
            name="ck_flashcard_decks_source_scope_json",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_flashcard_decks"),
        schema="study",
    )
    op.create_table(
        "flashcards",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("deck_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_point_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("front", sa.Text(), nullable=False),
        sa.Column("back", sa.Text(), nullable=False),
        sa.Column("ease_factor", sa.Numeric(4, 2), nullable=False, server_default=sa.text("2.50")),
        sa.Column("interval_days", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("repetition_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'active'")),
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
        sa.CheckConstraint("interval_days >= 0", name="ck_flashcards_interval_days_nonnegative"),
        sa.CheckConstraint(
            "repetition_count >= 0", name="ck_flashcards_repetition_count_nonnegative"
        ),
        sa.CheckConstraint("status IN ('active', 'suspended')", name="ck_flashcards_status_values"),
        sa.CheckConstraint("ease_factor >= 1.30", name="ck_flashcards_ease_factor_minimum"),
        sa.PrimaryKeyConstraint("id", name="pk_flashcards"),
        schema="study",
    )
    op.create_table(
        "flashcard_sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("flashcard_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("material_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("index_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("locator", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "flashcard_id", "chunk_id", name="uq_flashcard_sources_flashcard_id_chunk_id"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(locator) = 'object'", name="ck_flashcard_sources_locator_json"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_flashcard_sources"),
        schema="study",
    )
    op.create_table(
        "flashcard_reviews",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("flashcard_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("previous_state", postgresql.JSONB(), nullable=False),
        sa.Column("new_state", postgresql.JSONB(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.UniqueConstraint(
            "owner_user_id",
            "flashcard_id",
            "idempotency_key",
            name="uq_flashcard_reviews_owner_user_id_flashcard_id_idempotency_key",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(previous_state) = 'object'",
            name="ck_flashcard_reviews_previous_state_json",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(new_state) = 'object'", name="ck_flashcard_reviews_new_state_json"
        ),
        sa.CheckConstraint("rating BETWEEN 0 AND 3", name="ck_flashcard_reviews_rating_range"),
        sa.PrimaryKeyConstraint("id", name="pk_flashcard_reviews"),
        schema="study",
    )
    op.create_table(
        "mastery_states",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_point_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mastery_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("estimator_version", sa.String(60), nullable=False),
        sa.Column("last_practiced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "owner_user_id",
            "knowledge_point_id",
            name="uq_mastery_states_owner_user_id_knowledge_point_id",
        ),
        sa.CheckConstraint(
            "mastery_score BETWEEN 0 AND 1", name="ck_mastery_states_mastery_score_range"
        ),
        sa.CheckConstraint(
            "evidence_count >= 0", name="ck_mastery_states_evidence_count_nonnegative"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mastery_states"),
        schema="study",
    )
    op.create_table(
        "study_plans",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("generator_profile", sa.String(128), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'completed', 'archived')",
            name="ck_study_plans_status_values",
        ),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date >= start_date", name="ck_study_plans_date_order"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_study_plans"),
        schema="study",
    )
    op.create_table(
        "study_plan_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("study_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_point_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("task_type", sa.String(24), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("scheduled_for", sa.Date(), nullable=False),
        sa.Column("estimated_minutes", sa.SmallInteger(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "estimated_minutes > 0", name="ck_study_plan_items_estimated_minutes_positive"
        ),
        sa.CheckConstraint(
            "task_type IN ('read', 'quiz', 'review', 'note')",
            name="ck_study_plan_items_task_type_values",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'done', 'skipped')", name="ck_study_plan_items_status_values"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_study_plan_items"),
        schema="study",
    )
    op.create_table(
        "learning_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("event_key", sa.String(160), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subject_type", sa.String(50), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("event_key", name="uq_learning_events_event_key"),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name="ck_learning_events_payload_json"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_learning_events"),
        schema="study",
    )
    op.create_table(
        "ai_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(40), nullable=False),
        sa.Column("resource_type", sa.String(40), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("progress", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("attempt_count", sa.SmallInteger(), nullable=False),
        sa.Column("max_attempts", sa.SmallInteger(), nullable=False),
        sa.Column("fencing", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("lease_token_hash", sa.CHAR(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_ai_jobs_id_owner_user_id"),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_ai_jobs_payload_json"),
        sa.CheckConstraint("jsonb_typeof(result) = 'object'", name="ck_ai_jobs_result_json"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_ai_jobs_attempt_count_nonnegative"),
        sa.CheckConstraint("max_attempts > 0", name="ck_ai_jobs_max_attempts_positive"),
        sa.CheckConstraint("fencing >= 0", name="ck_ai_jobs_fencing_nonnegative"),
        sa.CheckConstraint(
            "job_type IN ('ingest', 'notes', 'quiz', 'flashcards', 'grade', 'cleanup')",
            name="ck_ai_jobs_job_type_values",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_ai_jobs_status_values",
        ),
        sa.CheckConstraint("progress BETWEEN 0 AND 100", name="ck_ai_jobs_progress_range"),
        sa.PrimaryKeyConstraint("id", name="pk_ai_jobs"),
        schema="study",
    )
    op.create_table(
        "ai_job_attempts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_no", sa.SmallInteger(), nullable=False),
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("fencing", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("usage", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint("job_id", "attempt_no", name="uq_ai_job_attempts_job_id_attempt_no"),
        sa.CheckConstraint("attempt_no > 0", name="ck_ai_job_attempts_attempt_no_positive"),
        sa.CheckConstraint("fencing >= 0", name="ck_ai_job_attempts_fencing_nonnegative"),
        sa.CheckConstraint("jsonb_typeof(usage) = 'object'", name="ck_ai_job_attempts_usage_json"),
        sa.CheckConstraint(
            "outcome IN ('success', 'retry', 'failed', 'cancelled')",
            name="ck_ai_job_attempts_outcome_values",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_job_attempts"),
        schema="study",
    )
    op.create_table(
        "ai_workflow_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("graph_name", sa.String(64), nullable=False),
        sa.Column("graph_version", sa.String(50), nullable=False),
        sa.Column("thread_id", sa.String(128), nullable=False),
        sa.Column("checkpoint_namespace", sa.String(128), nullable=False),
        sa.Column("last_checkpoint_id", sa.String(256), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column(
            "source_scope",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("last_node", sa.String(80), nullable=True),
        sa.Column("model_profile", sa.String(128), nullable=True),
        sa.Column("prompt_version", sa.String(50), nullable=True),
        sa.Column("usage", postgresql.JSONB(), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("id", "conversation_id", name="uq_ai_workflow_runs_id_conversation_id"),
        sa.CheckConstraint(
            "jsonb_typeof(source_scope) = 'object'", name="ck_ai_workflow_runs_source_scope_json"
        ),
        sa.CheckConstraint("jsonb_typeof(usage) = 'object'", name="ck_ai_workflow_runs_usage_json"),
        sa.CheckConstraint(
            "status IN ('created', 'running', 'awaiting_student', 'completed', 'failed', 'cancelled')",
            name="ck_ai_workflow_runs_status_values",
        ),
        sa.CheckConstraint(
            "(conversation_id IS NULL) <> (job_id IS NULL)",
            name="ck_ai_workflow_runs_single_subject",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_workflow_runs"),
        schema="study",
    )
    op.create_table(
        "outbox_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_key", sa.String(160), nullable=False),
        sa.Column("topic", sa.String(60), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatch_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("event_key", name="uq_outbox_events_event_key"),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name="ck_outbox_events_payload_json"
        ),
        sa.CheckConstraint(
            "dispatch_attempts >= 0", name="ck_outbox_events_dispatch_attempts_nonnegative"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_events"),
        schema="study",
    )
    op.create_table(
        "idempotency_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("route_scope", sa.String(140), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.CHAR(64), nullable=False),
        sa.Column("response_status", sa.SmallInteger(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(), nullable=True),
        sa.Column("resource_type", sa.String(40), nullable=True),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "user_id", "route_scope", "key", name="uq_idempotency_keys_user_id_route_scope_key"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(response_body) = 'object'", name="ck_idempotency_keys_response_body_json"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_idempotency_keys"),
        schema="study",
    )
    op.create_table(
        "audit_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("target_type", sa.String(60), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(\"metadata\") = 'object'", name="ck_audit_logs_metadata_json"
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'denied', 'error')", name="ck_audit_logs_outcome_values"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
        schema="study",
    )
    op.create_foreign_key(
        "fk_user_sessions_user_id",
        "user_sessions",
        "users",
        ["user_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_courses_owner_user_id",
        "courses",
        "users",
        ["owner_user_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_materials_owner_user_id",
        "materials",
        "users",
        ["owner_user_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_materials_course_id_owner_user_id",
        "materials",
        "courses",
        ["course_id", "owner_user_id"],
        ["id", "owner_user_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_material_versions_material_id",
        "material_versions",
        "materials",
        ["material_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_material_sections_material_version_id",
        "material_sections",
        "material_versions",
        ["material_version_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_material_sections_parent_section_id_material_version_id",
        "material_sections",
        "material_sections",
        ["parent_section_id", "material_version_id"],
        ["id", "material_version_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_index_versions_material_version_id",
        "index_versions",
        "material_versions",
        ["material_version_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_material_chunks_material_version_id",
        "material_chunks",
        "material_versions",
        ["material_version_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_material_chunks_index_version_id_material_version_id",
        "material_chunks",
        "index_versions",
        ["index_version_id", "material_version_id"],
        ["id", "material_version_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_material_chunks_section_id_material_version_id",
        "material_chunks",
        "material_sections",
        ["section_id", "material_version_id"],
        ["id", "material_version_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_chunk_embeddings_chunk_id",
        "chunk_embeddings",
        "material_chunks",
        ["chunk_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_knowledge_points_course_id",
        "knowledge_points",
        "courses",
        ["course_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_knowledge_points_parent_id_course_id",
        "knowledge_points",
        "knowledge_points",
        ["parent_id", "course_id"],
        ["id", "course_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_knowledge_point_sources_knowledge_point_id",
        "knowledge_point_sources",
        "knowledge_points",
        ["knowledge_point_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_knowledge_point_sources_chunk_id_index_version_id_m_f2fc4b7b",
        "knowledge_point_sources",
        "material_chunks",
        ["chunk_id", "index_version_id", "material_version_id"],
        ["id", "index_version_id", "material_version_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_source_questions_material_version_id",
        "source_questions",
        "material_versions",
        ["material_version_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_source_questions_section_id_material_version_id",
        "source_questions",
        "material_sections",
        ["section_id", "material_version_id"],
        ["id", "material_version_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_conversations_owner_user_id",
        "conversations",
        "users",
        ["owner_user_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_conversations_course_id_owner_user_id",
        "conversations",
        "courses",
        ["course_id", "owner_user_id"],
        ["id", "owner_user_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_messages_conversation_id",
        "messages",
        "conversations",
        ["conversation_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_messages_workflow_run_id_conversation_id",
        "messages",
        "ai_workflow_runs",
        ["workflow_run_id", "conversation_id"],
        ["id", "conversation_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_message_citations_message_id",
        "message_citations",
        "messages",
        ["message_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_message_citations_chunk_id_index_version_id_materia_e555d84b",
        "message_citations",
        "material_chunks",
        ["chunk_id", "index_version_id", "material_version_id"],
        ["id", "index_version_id", "material_version_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_notes_owner_user_id",
        "notes",
        "users",
        ["owner_user_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_notes_course_id_owner_user_id",
        "notes",
        "courses",
        ["course_id", "owner_user_id"],
        ["id", "owner_user_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_note_citations_note_id",
        "note_citations",
        "notes",
        ["note_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_note_citations_chunk_id_index_version_id_material_version_id",
        "note_citations",
        "material_chunks",
        ["chunk_id", "index_version_id", "material_version_id"],
        ["id", "index_version_id", "material_version_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_quizzes_owner_user_id",
        "quizzes",
        "users",
        ["owner_user_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_quizzes_course_id_owner_user_id",
        "quizzes",
        "courses",
        ["course_id", "owner_user_id"],
        ["id", "owner_user_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_quiz_items_quiz_id",
        "quiz_items",
        "quizzes",
        ["quiz_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_quiz_items_knowledge_point_id",
        "quiz_items",
        "knowledge_points",
        ["knowledge_point_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_quiz_item_sources_quiz_item_id",
        "quiz_item_sources",
        "quiz_items",
        ["quiz_item_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_quiz_item_sources_chunk_id_index_version_id_materia_b759fde4",
        "quiz_item_sources",
        "material_chunks",
        ["chunk_id", "index_version_id", "material_version_id"],
        ["id", "index_version_id", "material_version_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_quiz_attempts_owner_user_id",
        "quiz_attempts",
        "users",
        ["owner_user_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_quiz_attempts_quiz_id_owner_user_id",
        "quiz_attempts",
        "quizzes",
        ["quiz_id", "owner_user_id"],
        ["id", "owner_user_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_quiz_responses_attempt_id_quiz_id",
        "quiz_responses",
        "quiz_attempts",
        ["attempt_id", "quiz_id"],
        ["id", "quiz_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_quiz_responses_quiz_item_id_quiz_id",
        "quiz_responses",
        "quiz_items",
        ["quiz_item_id", "quiz_id"],
        ["id", "quiz_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_wrong_questions_owner_user_id",
        "wrong_questions",
        "users",
        ["owner_user_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_wrong_questions_quiz_item_id",
        "wrong_questions",
        "quiz_items",
        ["quiz_item_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_wrong_questions_last_response_id_quiz_item_id",
        "wrong_questions",
        "quiz_responses",
        ["last_response_id", "quiz_item_id"],
        ["id", "quiz_item_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_flashcard_decks_owner_user_id",
        "flashcard_decks",
        "users",
        ["owner_user_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_flashcard_decks_course_id_owner_user_id",
        "flashcard_decks",
        "courses",
        ["course_id", "owner_user_id"],
        ["id", "owner_user_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_flashcards_deck_id",
        "flashcards",
        "flashcard_decks",
        ["deck_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_flashcards_knowledge_point_id",
        "flashcards",
        "knowledge_points",
        ["knowledge_point_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_flashcard_sources_flashcard_id",
        "flashcard_sources",
        "flashcards",
        ["flashcard_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_flashcard_sources_chunk_id_index_version_id_materia_e2a29253",
        "flashcard_sources",
        "material_chunks",
        ["chunk_id", "index_version_id", "material_version_id"],
        ["id", "index_version_id", "material_version_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_flashcard_reviews_flashcard_id",
        "flashcard_reviews",
        "flashcards",
        ["flashcard_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_flashcard_reviews_owner_user_id",
        "flashcard_reviews",
        "users",
        ["owner_user_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_mastery_states_owner_user_id",
        "mastery_states",
        "users",
        ["owner_user_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_mastery_states_knowledge_point_id",
        "mastery_states",
        "knowledge_points",
        ["knowledge_point_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_study_plans_owner_user_id",
        "study_plans",
        "users",
        ["owner_user_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_study_plans_course_id_owner_user_id",
        "study_plans",
        "courses",
        ["course_id", "owner_user_id"],
        ["id", "owner_user_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_study_plan_items_study_plan_id",
        "study_plan_items",
        "study_plans",
        ["study_plan_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_study_plan_items_knowledge_point_id",
        "study_plan_items",
        "knowledge_points",
        ["knowledge_point_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_learning_events_owner_user_id",
        "learning_events",
        "users",
        ["owner_user_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_learning_events_course_id_owner_user_id",
        "learning_events",
        "courses",
        ["course_id", "owner_user_id"],
        ["id", "owner_user_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_ai_jobs_owner_user_id",
        "ai_jobs",
        "users",
        ["owner_user_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_ai_job_attempts_job_id",
        "ai_job_attempts",
        "ai_jobs",
        ["job_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_ai_workflow_runs_owner_user_id",
        "ai_workflow_runs",
        "users",
        ["owner_user_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_ai_workflow_runs_conversation_id_owner_user_id",
        "ai_workflow_runs",
        "conversations",
        ["conversation_id", "owner_user_id"],
        ["id", "owner_user_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_ai_workflow_runs_job_id_owner_user_id",
        "ai_workflow_runs",
        "ai_jobs",
        ["job_id", "owner_user_id"],
        ["id", "owner_user_id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_outbox_events_job_id",
        "outbox_events",
        "ai_jobs",
        ["job_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_idempotency_keys_user_id",
        "idempotency_keys",
        "users",
        ["user_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_audit_logs_actor_user_id",
        "audit_logs",
        "users",
        ["actor_user_id"],
        ["id"],
        source_schema="study",
        referent_schema="study",
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_user_sessions_user_expires",
        "user_sessions",
        ["user_id", sa.text("expires_at DESC")],
        schema="study",
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "ix_courses_owner_created",
        "courses",
        ["owner_user_id", sa.text("created_at DESC")],
        schema="study",
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_materials_owner_created",
        "materials",
        ["owner_user_id", sa.text("created_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_materials_course_created",
        "materials",
        ["course_id", sa.text("created_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_material_versions_current",
        "material_versions",
        ["material_id"],
        schema="study",
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "ix_material_versions_material_created",
        "material_versions",
        ["material_id", sa.text("created_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_material_sections_version_parent",
        "material_sections",
        ["material_version_id", "parent_section_id"],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_index_versions_active",
        "index_versions",
        ["material_version_id"],
        schema="study",
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        "ix_material_chunks_version_index",
        "material_chunks",
        ["material_version_id", "index_version_id"],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_material_chunks_lexical",
        "material_chunks",
        ["lexical"],
        schema="study",
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_chunk_embeddings_cosine_hnsw",
        "chunk_embeddings",
        ["embedding"],
        schema="study",
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_chunk_embeddings_model",
        "chunk_embeddings",
        ["model_profile_id"],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_knowledge_points_course_status",
        "knowledge_points",
        ["course_id", "status"],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_conversations_owner_created",
        "conversations",
        ["owner_user_id", sa.text("created_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_conversations_owner_course_updated",
        "conversations",
        ["owner_user_id", "course_id", sa.text("updated_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_messages_conversation_created",
        "messages",
        ["conversation_id", "created_at"],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_messages_workflow_run", "messages", ["workflow_run_id"], schema="study", unique=False
    )
    op.create_index(
        "ix_messages_assistant_run",
        "messages",
        ["workflow_run_id"],
        schema="study",
        unique=True,
        postgresql_where=sa.text("role = 'assistant' AND workflow_run_id IS NOT NULL"),
    )
    op.create_index(
        "ix_notes_owner_created",
        "notes",
        ["owner_user_id", sa.text("created_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_notes_owner_course_created",
        "notes",
        ["owner_user_id", "course_id", sa.text("created_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_quizzes_owner_created",
        "quizzes",
        ["owner_user_id", sa.text("created_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_quizzes_owner_course_created",
        "quizzes",
        ["owner_user_id", "course_id", sa.text("created_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_quiz_attempts_owner_created",
        "quiz_attempts",
        ["owner_user_id", sa.text("created_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_wrong_questions_owner_created",
        "wrong_questions",
        ["owner_user_id", sa.text("created_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_flashcard_decks_owner_created",
        "flashcard_decks",
        ["owner_user_id", sa.text("created_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_flashcard_decks_owner_course_created",
        "flashcard_decks",
        ["owner_user_id", "course_id", sa.text("created_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_flashcards_deck_due",
        "flashcards",
        ["deck_id", "due_at"],
        schema="study",
        unique=False,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_mastery_states_owner_created",
        "mastery_states",
        ["owner_user_id", sa.text("created_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_study_plans_owner_created",
        "study_plans",
        ["owner_user_id", sa.text("created_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_study_plans_owner_course_status",
        "study_plans",
        ["owner_user_id", "course_id", "status"],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_study_plan_items_plan_scheduled",
        "study_plan_items",
        ["study_plan_id", "scheduled_for"],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_learning_events_owner_created",
        "learning_events",
        ["owner_user_id", sa.text("created_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_learning_events_owner_course_occurred",
        "learning_events",
        ["owner_user_id", "course_id", sa.text("occurred_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_learning_events_type_occurred",
        "learning_events",
        ["event_type", "occurred_at"],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_ai_jobs_owner_created",
        "ai_jobs",
        ["owner_user_id", sa.text("created_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_ai_jobs_status_next_run",
        "ai_jobs",
        ["status", "next_run_at"],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_ai_workflow_runs_owner_created",
        "ai_workflow_runs",
        ["owner_user_id", sa.text("created_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_ai_workflow_runs_conversation_created",
        "ai_workflow_runs",
        ["conversation_id", sa.text("created_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_ai_workflow_runs_job", "ai_workflow_runs", ["job_id"], schema="study", unique=False
    )
    op.create_index(
        "ix_ai_workflow_runs_thread_created",
        "ai_workflow_runs",
        ["thread_id", sa.text("created_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_outbox_events_available",
        "outbox_events",
        ["available_at"],
        schema="study",
        unique=False,
        postgresql_where=sa.text("delivered_at IS NULL"),
    )
    op.create_index(
        "ix_audit_logs_actor_created",
        "audit_logs",
        ["actor_user_id", sa.text("created_at DESC")],
        schema="study",
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_action_created",
        "audit_logs",
        ["action", sa.text("created_at DESC")],
        schema="study",
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_action_created", table_name="audit_logs", schema="study")
    op.drop_index("ix_audit_logs_actor_created", table_name="audit_logs", schema="study")
    op.drop_index("ix_outbox_events_available", table_name="outbox_events", schema="study")
    op.drop_index(
        "ix_ai_workflow_runs_thread_created", table_name="ai_workflow_runs", schema="study"
    )
    op.drop_index("ix_ai_workflow_runs_job", table_name="ai_workflow_runs", schema="study")
    op.drop_index(
        "ix_ai_workflow_runs_conversation_created", table_name="ai_workflow_runs", schema="study"
    )
    op.drop_index(
        "ix_ai_workflow_runs_owner_created", table_name="ai_workflow_runs", schema="study"
    )
    op.drop_index("ix_ai_jobs_status_next_run", table_name="ai_jobs", schema="study")
    op.drop_index("ix_ai_jobs_owner_created", table_name="ai_jobs", schema="study")
    op.drop_index("ix_learning_events_type_occurred", table_name="learning_events", schema="study")
    op.drop_index(
        "ix_learning_events_owner_course_occurred", table_name="learning_events", schema="study"
    )
    op.drop_index("ix_learning_events_owner_created", table_name="learning_events", schema="study")
    op.drop_index(
        "ix_study_plan_items_plan_scheduled", table_name="study_plan_items", schema="study"
    )
    op.drop_index("ix_study_plans_owner_course_status", table_name="study_plans", schema="study")
    op.drop_index("ix_study_plans_owner_created", table_name="study_plans", schema="study")
    op.drop_index("ix_mastery_states_owner_created", table_name="mastery_states", schema="study")
    op.drop_index("ix_flashcards_deck_due", table_name="flashcards", schema="study")
    op.drop_index(
        "ix_flashcard_decks_owner_course_created", table_name="flashcard_decks", schema="study"
    )
    op.drop_index("ix_flashcard_decks_owner_created", table_name="flashcard_decks", schema="study")
    op.drop_index("ix_wrong_questions_owner_created", table_name="wrong_questions", schema="study")
    op.drop_index("ix_quiz_attempts_owner_created", table_name="quiz_attempts", schema="study")
    op.drop_index("ix_quizzes_owner_course_created", table_name="quizzes", schema="study")
    op.drop_index("ix_quizzes_owner_created", table_name="quizzes", schema="study")
    op.drop_index("ix_notes_owner_course_created", table_name="notes", schema="study")
    op.drop_index("ix_notes_owner_created", table_name="notes", schema="study")
    op.drop_index("ix_messages_assistant_run", table_name="messages", schema="study")
    op.drop_index("ix_messages_workflow_run", table_name="messages", schema="study")
    op.drop_index("ix_messages_conversation_created", table_name="messages", schema="study")
    op.drop_index(
        "ix_conversations_owner_course_updated", table_name="conversations", schema="study"
    )
    op.drop_index("ix_conversations_owner_created", table_name="conversations", schema="study")
    op.drop_index(
        "ix_knowledge_points_course_status", table_name="knowledge_points", schema="study"
    )
    op.drop_index("ix_chunk_embeddings_model", table_name="chunk_embeddings", schema="study")
    op.drop_index("ix_chunk_embeddings_cosine_hnsw", table_name="chunk_embeddings", schema="study")
    op.drop_index("ix_material_chunks_lexical", table_name="material_chunks", schema="study")
    op.drop_index("ix_material_chunks_version_index", table_name="material_chunks", schema="study")
    op.drop_index("ix_index_versions_active", table_name="index_versions", schema="study")
    op.drop_index(
        "ix_material_sections_version_parent", table_name="material_sections", schema="study"
    )
    op.drop_index(
        "ix_material_versions_material_created", table_name="material_versions", schema="study"
    )
    op.drop_index("ix_material_versions_current", table_name="material_versions", schema="study")
    op.drop_index("ix_materials_course_created", table_name="materials", schema="study")
    op.drop_index("ix_materials_owner_created", table_name="materials", schema="study")
    op.drop_index("ix_courses_owner_created", table_name="courses", schema="study")
    op.drop_index("ix_user_sessions_user_expires", table_name="user_sessions", schema="study")
    op.drop_constraint(
        "fk_audit_logs_actor_user_id", "audit_logs", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_idempotency_keys_user_id", "idempotency_keys", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_outbox_events_job_id", "outbox_events", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_ai_workflow_runs_job_id_owner_user_id",
        "ai_workflow_runs",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_ai_workflow_runs_conversation_id_owner_user_id",
        "ai_workflow_runs",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_ai_workflow_runs_owner_user_id", "ai_workflow_runs", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_ai_job_attempts_job_id", "ai_job_attempts", type_="foreignkey", schema="study"
    )
    op.drop_constraint("fk_ai_jobs_owner_user_id", "ai_jobs", type_="foreignkey", schema="study")
    op.drop_constraint(
        "fk_learning_events_course_id_owner_user_id",
        "learning_events",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_learning_events_owner_user_id", "learning_events", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_study_plan_items_knowledge_point_id",
        "study_plan_items",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_study_plan_items_study_plan_id", "study_plan_items", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_study_plans_course_id_owner_user_id", "study_plans", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_study_plans_owner_user_id", "study_plans", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_mastery_states_knowledge_point_id", "mastery_states", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_mastery_states_owner_user_id", "mastery_states", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_flashcard_reviews_owner_user_id",
        "flashcard_reviews",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_flashcard_reviews_flashcard_id", "flashcard_reviews", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_flashcard_sources_chunk_id_index_version_id_materia_e2a29253",
        "flashcard_sources",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_flashcard_sources_flashcard_id", "flashcard_sources", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_flashcards_knowledge_point_id", "flashcards", type_="foreignkey", schema="study"
    )
    op.drop_constraint("fk_flashcards_deck_id", "flashcards", type_="foreignkey", schema="study")
    op.drop_constraint(
        "fk_flashcard_decks_course_id_owner_user_id",
        "flashcard_decks",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_flashcard_decks_owner_user_id", "flashcard_decks", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_wrong_questions_last_response_id_quiz_item_id",
        "wrong_questions",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_wrong_questions_quiz_item_id", "wrong_questions", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_wrong_questions_owner_user_id", "wrong_questions", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_quiz_responses_quiz_item_id_quiz_id",
        "quiz_responses",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_quiz_responses_attempt_id_quiz_id", "quiz_responses", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_quiz_attempts_quiz_id_owner_user_id",
        "quiz_attempts",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_quiz_attempts_owner_user_id", "quiz_attempts", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_quiz_item_sources_chunk_id_index_version_id_materia_b759fde4",
        "quiz_item_sources",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_quiz_item_sources_quiz_item_id", "quiz_item_sources", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_quiz_items_knowledge_point_id", "quiz_items", type_="foreignkey", schema="study"
    )
    op.drop_constraint("fk_quiz_items_quiz_id", "quiz_items", type_="foreignkey", schema="study")
    op.drop_constraint(
        "fk_quizzes_course_id_owner_user_id", "quizzes", type_="foreignkey", schema="study"
    )
    op.drop_constraint("fk_quizzes_owner_user_id", "quizzes", type_="foreignkey", schema="study")
    op.drop_constraint(
        "fk_note_citations_chunk_id_index_version_id_material_version_id",
        "note_citations",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_note_citations_note_id", "note_citations", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_notes_course_id_owner_user_id", "notes", type_="foreignkey", schema="study"
    )
    op.drop_constraint("fk_notes_owner_user_id", "notes", type_="foreignkey", schema="study")
    op.drop_constraint(
        "fk_message_citations_chunk_id_index_version_id_materia_e555d84b",
        "message_citations",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_message_citations_message_id", "message_citations", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_messages_workflow_run_id_conversation_id",
        "messages",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_messages_conversation_id", "messages", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_conversations_course_id_owner_user_id",
        "conversations",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_conversations_owner_user_id", "conversations", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_source_questions_section_id_material_version_id",
        "source_questions",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_source_questions_material_version_id",
        "source_questions",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_knowledge_point_sources_chunk_id_index_version_id_m_f2fc4b7b",
        "knowledge_point_sources",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_knowledge_point_sources_knowledge_point_id",
        "knowledge_point_sources",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_knowledge_points_parent_id_course_id",
        "knowledge_points",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_knowledge_points_course_id", "knowledge_points", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_chunk_embeddings_chunk_id", "chunk_embeddings", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_material_chunks_section_id_material_version_id",
        "material_chunks",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_material_chunks_index_version_id_material_version_id",
        "material_chunks",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_material_chunks_material_version_id",
        "material_chunks",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_index_versions_material_version_id",
        "index_versions",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_material_sections_parent_section_id_material_version_id",
        "material_sections",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_material_sections_material_version_id",
        "material_sections",
        type_="foreignkey",
        schema="study",
    )
    op.drop_constraint(
        "fk_material_versions_material_id", "material_versions", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_materials_course_id_owner_user_id", "materials", type_="foreignkey", schema="study"
    )
    op.drop_constraint(
        "fk_materials_owner_user_id", "materials", type_="foreignkey", schema="study"
    )
    op.drop_constraint("fk_courses_owner_user_id", "courses", type_="foreignkey", schema="study")
    op.drop_constraint(
        "fk_user_sessions_user_id", "user_sessions", type_="foreignkey", schema="study"
    )
    op.drop_table("audit_logs", schema="study")
    op.drop_table("idempotency_keys", schema="study")
    op.drop_table("outbox_events", schema="study")
    op.drop_table("ai_workflow_runs", schema="study")
    op.drop_table("ai_job_attempts", schema="study")
    op.drop_table("ai_jobs", schema="study")
    op.drop_table("learning_events", schema="study")
    op.drop_table("study_plan_items", schema="study")
    op.drop_table("study_plans", schema="study")
    op.drop_table("mastery_states", schema="study")
    op.drop_table("flashcard_reviews", schema="study")
    op.drop_table("flashcard_sources", schema="study")
    op.drop_table("flashcards", schema="study")
    op.drop_table("flashcard_decks", schema="study")
    op.drop_table("wrong_questions", schema="study")
    op.drop_table("quiz_responses", schema="study")
    op.drop_table("quiz_attempts", schema="study")
    op.drop_table("quiz_item_sources", schema="study")
    op.drop_table("quiz_items", schema="study")
    op.drop_table("quizzes", schema="study")
    op.drop_table("note_citations", schema="study")
    op.drop_table("notes", schema="study")
    op.drop_table("message_citations", schema="study")
    op.drop_table("messages", schema="study")
    op.drop_table("conversations", schema="study")
    op.drop_table("source_questions", schema="study")
    op.drop_table("knowledge_point_sources", schema="study")
    op.drop_table("knowledge_points", schema="study")
    op.drop_table("chunk_embeddings", schema="study")
    op.drop_table("material_chunks", schema="study")
    op.drop_table("index_versions", schema="study")
    op.drop_table("material_sections", schema="study")
    op.drop_table("material_versions", schema="study")
    op.drop_table("materials", schema="study")
    op.drop_table("courses", schema="study")
    op.drop_table("user_sessions", schema="study")
    op.drop_table("users", schema="study")
    # Framework-owned study_graph tables must be removed by its own teardown first.
    op.execute("DROP SCHEMA study_graph")
    # Keep study: it contains Alembic's own version table.
    # Shared citext/vector extensions are deliberately preserved.
