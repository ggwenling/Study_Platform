"""Quizzes database models; constraints mirror the initial migration."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from study_platform.persistence.base import Base


class Quiz(Base):
    __tablename__ = "quizzes"
    __table_args__ = (
        sa.UniqueConstraint("id", "owner_user_id", name="uq_quizzes_id_owner_user_id"),
        sa.CheckConstraint("difficulty BETWEEN 1 AND 5", name="ck_quizzes_difficulty_range"),
        sa.CheckConstraint(
            "jsonb_typeof(source_scope) IN ('object', 'array')", name="ck_quizzes_source_scope_json"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'ready', 'archived')", name="ck_quizzes_status_values"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["study.users.id"],
            name="fk_quizzes_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "owner_user_id"],
            ["study.courses.id", "study.courses.owner_user_id"],
            name="fk_quizzes_course_id_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_quizzes_owner_created", "owner_user_id", sa.text("created_at DESC"), unique=False
        ),
        sa.Index(
            "ix_quizzes_owner_course_created",
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
    status = sa.Column("status", sa.String(16), nullable=False)
    difficulty = sa.Column("difficulty", sa.SmallInteger(), nullable=True)
    source_scope = sa.Column("source_scope", postgresql.JSONB(), nullable=False)
    generator_profile = sa.Column("generator_profile", sa.String(128), nullable=True)
    prompt_version = sa.Column("prompt_version", sa.String(50), nullable=True)
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


class QuizItem(Base):
    __tablename__ = "quiz_items"
    __table_args__ = (
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
        sa.ForeignKeyConstraint(
            ["quiz_id"],
            ["study.quizzes.id"],
            name="fk_quiz_items_quiz_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_point_id"],
            ["study.knowledge_points.id"],
            name="fk_quiz_items_knowledge_point_id",
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
    quiz_id = sa.Column("quiz_id", postgresql.UUID(as_uuid=True), nullable=False)
    knowledge_point_id = sa.Column(
        "knowledge_point_id", postgresql.UUID(as_uuid=True), nullable=True
    )
    ordinal = sa.Column("ordinal", sa.Integer(), nullable=False)
    item_type = sa.Column("item_type", sa.String(24), nullable=False)
    stem = sa.Column("stem", sa.Text(), nullable=False)
    options = sa.Column("options", postgresql.JSONB(), nullable=True)
    answer_key = sa.Column("answer_key", postgresql.JSONB(), nullable=False)
    rubric = sa.Column("rubric", postgresql.JSONB(), nullable=True)
    explanation = sa.Column("explanation", sa.Text(), nullable=True)
    difficulty = sa.Column("difficulty", sa.SmallInteger(), nullable=True)
    max_score = sa.Column(
        "max_score", sa.Numeric(8, 2), nullable=False, server_default=sa.text("1")
    )
    created_at = sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


class QuizItemSource(Base):
    __tablename__ = "quiz_item_sources"
    __table_args__ = (
        sa.UniqueConstraint(
            "quiz_item_id", "chunk_id", name="uq_quiz_item_sources_quiz_item_id_chunk_id"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(locator) = 'object'", name="ck_quiz_item_sources_locator_json"
        ),
        sa.ForeignKeyConstraint(
            ["quiz_item_id"],
            ["study.quiz_items.id"],
            name="fk_quiz_item_sources_quiz_item_id",
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
            name="fk_quiz_item_sources_chunk_id_index_version_id_materia_b759fde4",
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
    quiz_item_id = sa.Column("quiz_item_id", postgresql.UUID(as_uuid=True), nullable=False)
    chunk_id = sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False)
    material_version_id = sa.Column(
        "material_version_id", postgresql.UUID(as_uuid=True), nullable=False
    )
    index_version_id = sa.Column("index_version_id", postgresql.UUID(as_uuid=True), nullable=False)
    locator = sa.Column("locator", postgresql.JSONB(), nullable=False)
    created_at = sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"
    __table_args__ = (
        sa.UniqueConstraint("id", "quiz_id", name="uq_quiz_attempts_id_quiz_id"),
        sa.CheckConstraint("total_score >= 0", name="ck_quiz_attempts_total_score_nonnegative"),
        sa.CheckConstraint("max_score > 0", name="ck_quiz_attempts_max_score_positive"),
        sa.CheckConstraint(
            "status IN ('in_progress', 'submitted', 'graded')",
            name="ck_quiz_attempts_status_values",
        ),
        sa.CheckConstraint("total_score <= max_score", name="ck_quiz_attempts_total_score_range"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["study.users.id"],
            name="fk_quiz_attempts_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["quiz_id", "owner_user_id"],
            ["study.quizzes.id", "study.quizzes.owner_user_id"],
            name="fk_quiz_attempts_quiz_id_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_quiz_attempts_owner_created",
            "owner_user_id",
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
    quiz_id = sa.Column("quiz_id", postgresql.UUID(as_uuid=True), nullable=False)
    owner_user_id = sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False)
    status = sa.Column("status", sa.String(16), nullable=False)
    started_at = sa.Column("started_at", sa.DateTime(timezone=True), nullable=False)
    submitted_at = sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True)
    graded_at = sa.Column("graded_at", sa.DateTime(timezone=True), nullable=True)
    total_score = sa.Column("total_score", sa.Numeric(10, 2), nullable=True)
    max_score = sa.Column("max_score", sa.Numeric(10, 2), nullable=False)
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


class QuizResponse(Base):
    __tablename__ = "quiz_responses"
    __table_args__ = (
        sa.UniqueConstraint("id", "quiz_item_id", name="uq_quiz_responses_id_quiz_item_id"),
        sa.UniqueConstraint(
            "attempt_id", "quiz_item_id", name="uq_quiz_responses_attempt_id_quiz_item_id"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(answer) IN ('object', 'array', 'string', 'number', 'boolean')",
            name="ck_quiz_responses_answer_json",
        ),
        sa.CheckConstraint("score >= 0", name="ck_quiz_responses_score_nonnegative"),
        sa.CheckConstraint(
            "verdict IS NULL OR verdict IN ('correct', 'incorrect', 'pending')",
            name="ck_quiz_responses_verdict_values",
        ),
        sa.CheckConstraint(
            "verdict_source IS NULL OR verdict_source IN ('auto', 'user')",
            name="ck_quiz_responses_verdict_source_values",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_quiz_responses_confidence_range",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id", "quiz_id"],
            ["study.quiz_attempts.id", "study.quiz_attempts.quiz_id"],
            name="fk_quiz_responses_attempt_id_quiz_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["quiz_item_id", "quiz_id"],
            ["study.quiz_items.id", "study.quiz_items.quiz_id"],
            name="fk_quiz_responses_quiz_item_id_quiz_id",
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
    attempt_id = sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False)
    quiz_id = sa.Column("quiz_id", postgresql.UUID(as_uuid=True), nullable=False)
    quiz_item_id = sa.Column("quiz_item_id", postgresql.UUID(as_uuid=True), nullable=False)
    answer = sa.Column("answer", postgresql.JSONB(), nullable=False)
    score = sa.Column("score", sa.Numeric(8, 2), nullable=True)
    is_correct = sa.Column("is_correct", sa.Boolean(), nullable=True)
    feedback = sa.Column("feedback", sa.Text(), nullable=True)
    grader_profile = sa.Column("grader_profile", sa.String(128), nullable=True)
    confidence = sa.Column("confidence", sa.Numeric(5, 4), nullable=True)
    verdict = sa.Column("verdict", sa.String(16), nullable=True)
    verdict_source = sa.Column("verdict_source", sa.String(16), nullable=True)
    reference_answer_effective = sa.Column("reference_answer_effective", sa.Text(), nullable=True)
    graded_at = sa.Column("graded_at", sa.DateTime(timezone=True), nullable=True)
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


class WrongQuestion(Base):
    __tablename__ = "wrong_questions"
    __table_args__ = (
        sa.UniqueConstraint(
            "owner_user_id", "quiz_item_id", name="uq_wrong_questions_owner_user_id_quiz_item_id"
        ),
        sa.CheckConstraint("wrong_count >= 0", name="ck_wrong_questions_wrong_count_nonnegative"),
        sa.CheckConstraint(
            "status IN ('open', 'reviewing', 'mastered')", name="ck_wrong_questions_status_values"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["study.users.id"],
            name="fk_wrong_questions_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["quiz_item_id"],
            ["study.quiz_items.id"],
            name="fk_wrong_questions_quiz_item_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["last_response_id", "quiz_item_id"],
            ["study.quiz_responses.id", "study.quiz_responses.quiz_item_id"],
            name="fk_wrong_questions_last_response_id_quiz_item_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_wrong_questions_owner_created",
            "owner_user_id",
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
    quiz_item_id = sa.Column("quiz_item_id", postgresql.UUID(as_uuid=True), nullable=False)
    last_response_id = sa.Column("last_response_id", postgresql.UUID(as_uuid=True), nullable=True)
    wrong_count = sa.Column(
        "wrong_count", sa.Integer(), nullable=False, server_default=sa.text("1")
    )
    status = sa.Column("status", sa.String(16), nullable=False)
    last_wrong_at = sa.Column("last_wrong_at", sa.DateTime(timezone=True), nullable=False)
    next_review_at = sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True)
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
