"""Materials database models; constraints mirror the initial migration."""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from study_platform.persistence.base import Base


class MaterialVersion(Base):
    __tablename__ = "material_versions"
    __table_args__ = (
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
        sa.ForeignKeyConstraint(
            ["material_id"],
            ["study.materials.id"],
            name="fk_material_versions_material_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_material_versions_current",
            "material_id",
            unique=True,
            postgresql_where=sa.text("is_current"),
        ),
        sa.Index(
            "ix_material_versions_material_created",
            "material_id",
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
    material_id = sa.Column("material_id", postgresql.UUID(as_uuid=True), nullable=False)
    version_no = sa.Column("version_no", sa.Integer(), nullable=False)
    is_current = sa.Column(
        "is_current", sa.Boolean(), nullable=False, server_default=sa.text("false")
    )
    source_mime = sa.Column("source_mime", sa.String(100), nullable=False)
    source_sha256 = sa.Column("source_sha256", sa.CHAR(64), nullable=False)
    byte_size = sa.Column("byte_size", sa.BigInteger(), nullable=False)
    object_key = sa.Column("object_key", sa.Text(), nullable=False)
    page_count = sa.Column("page_count", sa.Integer(), nullable=True)
    parser_profile = sa.Column("parser_profile", sa.String(120), nullable=True)
    parse_status = sa.Column(
        "parse_status", sa.String(20), nullable=False, server_default=sa.text("'pending'")
    )
    failure_code = sa.Column("failure_code", sa.String(64), nullable=True)
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


class MaterialSection(Base):
    __tablename__ = "material_sections"
    __table_args__ = (
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
        sa.ForeignKeyConstraint(
            ["material_version_id"],
            ["study.material_versions.id"],
            name="fk_material_sections_material_version_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["parent_section_id", "material_version_id"],
            ["study.material_sections.id", "study.material_sections.material_version_id"],
            name="fk_material_sections_parent_section_id_material_version_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_material_sections_version_parent",
            "material_version_id",
            "parent_section_id",
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
    material_version_id = sa.Column(
        "material_version_id", postgresql.UUID(as_uuid=True), nullable=False
    )
    parent_section_id = sa.Column("parent_section_id", postgresql.UUID(as_uuid=True), nullable=True)
    ordinal = sa.Column("ordinal", sa.Integer(), nullable=False)
    heading = sa.Column("heading", sa.Text(), nullable=True)
    level = sa.Column("level", sa.SmallInteger(), nullable=False)
    text = sa.Column("text", sa.Text(), nullable=False)
    locator = sa.Column("locator", postgresql.JSONB(), nullable=False)
    confidence = sa.Column("confidence", sa.Numeric(5, 4), nullable=True)
    created_at = sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


class IndexVersion(Base):
    __tablename__ = "index_versions"
    __table_args__ = (
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
        sa.ForeignKeyConstraint(
            ["material_version_id"],
            ["study.material_versions.id"],
            name="fk_index_versions_material_version_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_index_versions_active",
            "material_version_id",
            unique=True,
            postgresql_where=sa.text("is_active"),
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
    material_version_id = sa.Column(
        "material_version_id", postgresql.UUID(as_uuid=True), nullable=False
    )
    generation = sa.Column("generation", sa.Integer(), nullable=False)
    pipeline_profile = sa.Column("pipeline_profile", sa.String(128), nullable=False)
    embedding_profile = sa.Column("embedding_profile", sa.String(128), nullable=False)
    embedding_dim = sa.Column(
        "embedding_dim", sa.SmallInteger(), nullable=False, server_default=sa.text("1024")
    )
    status = sa.Column("status", sa.String(16), nullable=False)
    is_active = sa.Column(
        "is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")
    )
    chunk_count = sa.Column(
        "chunk_count", sa.Integer(), nullable=False, server_default=sa.text("0")
    )
    manifest_sha256 = sa.Column("manifest_sha256", sa.CHAR(64), nullable=True)
    failure_code = sa.Column("failure_code", sa.String(64), nullable=True)
    ready_at = sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True)
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


class MaterialChunk(Base):
    __tablename__ = "material_chunks"
    __table_args__ = (
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
        sa.ForeignKeyConstraint(
            ["material_version_id"],
            ["study.material_versions.id"],
            name="fk_material_chunks_material_version_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["index_version_id", "material_version_id"],
            ["study.index_versions.id", "study.index_versions.material_version_id"],
            name="fk_material_chunks_index_version_id_material_version_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["section_id", "material_version_id"],
            ["study.material_sections.id", "study.material_sections.material_version_id"],
            name="fk_material_chunks_section_id_material_version_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_material_chunks_version_index",
            "material_version_id",
            "index_version_id",
            unique=False,
        ),
        sa.Index("ix_material_chunks_lexical", "lexical", unique=False, postgresql_using="gin"),
        {"schema": "study"},
    )

    id = sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        nullable=False,
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    index_version_id = sa.Column("index_version_id", postgresql.UUID(as_uuid=True), nullable=False)
    material_version_id = sa.Column(
        "material_version_id", postgresql.UUID(as_uuid=True), nullable=False
    )
    section_id = sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=True)
    group_id = sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True)
    ordinal = sa.Column("ordinal", sa.Integer(), nullable=False)
    text = sa.Column("text", sa.Text(), nullable=False)
    text_sha256 = sa.Column("text_sha256", sa.CHAR(64), nullable=False)
    locator = sa.Column("locator", postgresql.JSONB(), nullable=False)
    lexical = sa.Column("lexical", postgresql.TSVECTOR(), nullable=True)
    created_at = sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["study.material_chunks.id"],
            name="fk_chunk_embeddings_chunk_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_chunk_embeddings_cosine_hnsw",
            "embedding",
            unique=False,
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        sa.Index("ix_chunk_embeddings_model", "model_profile_id", unique=False),
        {"schema": "study"},
    )

    chunk_id = sa.Column(
        "chunk_id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
    )
    model_profile_id = sa.Column(
        "model_profile_id", sa.String(128), nullable=False, primary_key=True
    )
    embedding = sa.Column("embedding", Vector(1024), nullable=False)
    embedding_sha256 = sa.Column("embedding_sha256", sa.CHAR(64), nullable=True)
    created_at = sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


class KnowledgePoint(Base):
    __tablename__ = "knowledge_points"
    __table_args__ = (
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
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["study.courses.id"],
            name="fk_knowledge_points_course_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["parent_id", "course_id"],
            ["study.knowledge_points.id", "study.knowledge_points.course_id"],
            name="fk_knowledge_points_parent_id_course_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index("ix_knowledge_points_course_status", "course_id", "status", unique=False),
        {"schema": "study"},
    )

    id = sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        nullable=False,
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    course_id = sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False)
    parent_id = sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True)
    canonical_name = sa.Column("canonical_name", sa.String(255), nullable=False)
    description = sa.Column("description", sa.Text(), nullable=True)
    subject_tag = sa.Column("subject_tag", sa.String(80), nullable=True)
    difficulty = sa.Column("difficulty", sa.SmallInteger(), nullable=True)
    source = sa.Column("source", sa.String(16), nullable=False)
    confidence = sa.Column("confidence", sa.Numeric(5, 4), nullable=True)
    status = sa.Column("status", sa.String(16), nullable=False)
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


class KnowledgePointSource(Base):
    __tablename__ = "knowledge_point_sources"
    __table_args__ = (
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
        sa.ForeignKeyConstraint(
            ["knowledge_point_id"],
            ["study.knowledge_points.id"],
            name="fk_knowledge_point_sources_knowledge_point_id",
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
            name="fk_knowledge_point_sources_chunk_id_index_version_id_m_f2fc4b7b",
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
    knowledge_point_id = sa.Column(
        "knowledge_point_id", postgresql.UUID(as_uuid=True), nullable=False
    )
    chunk_id = sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False)
    material_version_id = sa.Column(
        "material_version_id", postgresql.UUID(as_uuid=True), nullable=False
    )
    index_version_id = sa.Column("index_version_id", postgresql.UUID(as_uuid=True), nullable=False)
    evidence_span = sa.Column("evidence_span", postgresql.JSONB(), nullable=True)
    confidence = sa.Column("confidence", sa.Numeric(5, 4), nullable=True)
    created_at = sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


class SourceQuestion(Base):
    __tablename__ = "source_questions"
    __table_args__ = (
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
        sa.ForeignKeyConstraint(
            ["material_version_id"],
            ["study.material_versions.id"],
            name="fk_source_questions_material_version_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["section_id", "material_version_id"],
            ["study.material_sections.id", "study.material_sections.material_version_id"],
            name="fk_source_questions_section_id_material_version_id",
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
    material_version_id = sa.Column(
        "material_version_id", postgresql.UUID(as_uuid=True), nullable=False
    )
    section_id = sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=True)
    ordinal = sa.Column("ordinal", sa.Integer(), nullable=False)
    question_type = sa.Column("question_type", sa.String(24), nullable=False)
    stem = sa.Column("stem", sa.Text(), nullable=False)
    options = sa.Column("options", postgresql.JSONB(), nullable=True)
    reference_answer = sa.Column("reference_answer", postgresql.JSONB(), nullable=True)
    solution = sa.Column("solution", sa.Text(), nullable=True)
    locator = sa.Column("locator", postgresql.JSONB(), nullable=False)
    confidence = sa.Column("confidence", sa.Numeric(5, 4), nullable=True)
    created_at = sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
