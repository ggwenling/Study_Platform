"""Learning database models; constraints mirror the initial migration."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from study_platform.persistence.base import Base


class FlashcardDeck(Base):
    __tablename__ = "flashcard_decks"
    __table_args__ = (
        sa.CheckConstraint(
            "jsonb_typeof(source_scope) IN ('object', 'array')",
            name="ck_flashcard_decks_source_scope_json",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["study.users.id"],
            name="fk_flashcard_decks_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "owner_user_id"],
            ["study.courses.id", "study.courses.owner_user_id"],
            name="fk_flashcard_decks_course_id_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_flashcard_decks_owner_created",
            "owner_user_id",
            sa.text("created_at DESC"),
            unique=False,
        ),
        sa.Index(
            "ix_flashcard_decks_owner_course_created",
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
    source_scope = sa.Column("source_scope", postgresql.JSONB(), nullable=False)
    generator_profile = sa.Column("generator_profile", sa.String(128), nullable=True)
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


class Flashcard(Base):
    __tablename__ = "flashcards"
    __table_args__ = (
        sa.CheckConstraint("interval_days >= 0", name="ck_flashcards_interval_days_nonnegative"),
        sa.CheckConstraint(
            "repetition_count >= 0", name="ck_flashcards_repetition_count_nonnegative"
        ),
        sa.CheckConstraint("status IN ('active', 'suspended')", name="ck_flashcards_status_values"),
        sa.CheckConstraint("ease_factor >= 1.30", name="ck_flashcards_ease_factor_minimum"),
        sa.ForeignKeyConstraint(
            ["deck_id"],
            ["study.flashcard_decks.id"],
            name="fk_flashcards_deck_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_point_id"],
            ["study.knowledge_points.id"],
            name="fk_flashcards_knowledge_point_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_flashcards_deck_due",
            "deck_id",
            "due_at",
            unique=False,
            postgresql_where=sa.text("status = 'active'"),
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
    deck_id = sa.Column("deck_id", postgresql.UUID(as_uuid=True), nullable=False)
    knowledge_point_id = sa.Column(
        "knowledge_point_id", postgresql.UUID(as_uuid=True), nullable=True
    )
    front = sa.Column("front", sa.Text(), nullable=False)
    back = sa.Column("back", sa.Text(), nullable=False)
    ease_factor = sa.Column(
        "ease_factor", sa.Numeric(4, 2), nullable=False, server_default=sa.text("2.50")
    )
    interval_days = sa.Column(
        "interval_days", sa.Integer(), nullable=False, server_default=sa.text("0")
    )
    repetition_count = sa.Column(
        "repetition_count", sa.Integer(), nullable=False, server_default=sa.text("0")
    )
    due_at = sa.Column("due_at", sa.DateTime(timezone=True), nullable=False)
    status = sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'active'"))
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


class FlashcardSource(Base):
    __tablename__ = "flashcard_sources"
    __table_args__ = (
        sa.UniqueConstraint(
            "flashcard_id", "chunk_id", name="uq_flashcard_sources_flashcard_id_chunk_id"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(locator) = 'object'", name="ck_flashcard_sources_locator_json"
        ),
        sa.ForeignKeyConstraint(
            ["flashcard_id"],
            ["study.flashcards.id"],
            name="fk_flashcard_sources_flashcard_id",
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
            name="fk_flashcard_sources_chunk_id_index_version_id_materia_e2a29253",
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
    flashcard_id = sa.Column("flashcard_id", postgresql.UUID(as_uuid=True), nullable=False)
    chunk_id = sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False)
    material_version_id = sa.Column(
        "material_version_id", postgresql.UUID(as_uuid=True), nullable=False
    )
    index_version_id = sa.Column("index_version_id", postgresql.UUID(as_uuid=True), nullable=False)
    locator = sa.Column("locator", postgresql.JSONB(), nullable=False)
    created_at = sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


class FlashcardReview(Base):
    __tablename__ = "flashcard_reviews"
    __table_args__ = (
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
        sa.ForeignKeyConstraint(
            ["flashcard_id"],
            ["study.flashcards.id"],
            name="fk_flashcard_reviews_flashcard_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["study.users.id"],
            name="fk_flashcard_reviews_owner_user_id",
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
    flashcard_id = sa.Column("flashcard_id", postgresql.UUID(as_uuid=True), nullable=False)
    owner_user_id = sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False)
    rating = sa.Column("rating", sa.SmallInteger(), nullable=False)
    previous_state = sa.Column("previous_state", postgresql.JSONB(), nullable=False)
    new_state = sa.Column("new_state", postgresql.JSONB(), nullable=False)
    reviewed_at = sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False)
    idempotency_key = sa.Column("idempotency_key", sa.String(128), nullable=False)


class MasteryState(Base):
    __tablename__ = "mastery_states"
    __table_args__ = (
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
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["study.users.id"],
            name="fk_mastery_states_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_point_id"],
            ["study.knowledge_points.id"],
            name="fk_mastery_states_knowledge_point_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_mastery_states_owner_created",
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
    knowledge_point_id = sa.Column(
        "knowledge_point_id", postgresql.UUID(as_uuid=True), nullable=False
    )
    mastery_score = sa.Column("mastery_score", sa.Numeric(5, 4), nullable=False)
    evidence_count = sa.Column(
        "evidence_count", sa.Integer(), nullable=False, server_default=sa.text("0")
    )
    estimator_version = sa.Column("estimator_version", sa.String(60), nullable=False)
    last_practiced_at = sa.Column("last_practiced_at", sa.DateTime(timezone=True), nullable=True)
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


class StudyPlan(Base):
    __tablename__ = "study_plans"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'completed', 'archived')",
            name="ck_study_plans_status_values",
        ),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date >= start_date", name="ck_study_plans_date_order"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["study.users.id"],
            name="fk_study_plans_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "owner_user_id"],
            ["study.courses.id", "study.courses.owner_user_id"],
            name="fk_study_plans_course_id_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_study_plans_owner_created",
            "owner_user_id",
            sa.text("created_at DESC"),
            unique=False,
        ),
        sa.Index(
            "ix_study_plans_owner_course_status",
            "owner_user_id",
            "course_id",
            "status",
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
    start_date = sa.Column("start_date", sa.Date(), nullable=False)
    end_date = sa.Column("end_date", sa.Date(), nullable=True)
    generator_profile = sa.Column("generator_profile", sa.String(128), nullable=True)
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


class StudyPlanItem(Base):
    __tablename__ = "study_plan_items"
    __table_args__ = (
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
        sa.ForeignKeyConstraint(
            ["study_plan_id"],
            ["study.study_plans.id"],
            name="fk_study_plan_items_study_plan_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_point_id"],
            ["study.knowledge_points.id"],
            name="fk_study_plan_items_knowledge_point_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_study_plan_items_plan_scheduled", "study_plan_id", "scheduled_for", unique=False
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
    study_plan_id = sa.Column("study_plan_id", postgresql.UUID(as_uuid=True), nullable=False)
    knowledge_point_id = sa.Column(
        "knowledge_point_id", postgresql.UUID(as_uuid=True), nullable=True
    )
    task_type = sa.Column("task_type", sa.String(24), nullable=False)
    title = sa.Column("title", sa.String(255), nullable=False)
    scheduled_for = sa.Column("scheduled_for", sa.Date(), nullable=False)
    estimated_minutes = sa.Column("estimated_minutes", sa.SmallInteger(), nullable=True)
    status = sa.Column("status", sa.String(16), nullable=False)
    completed_at = sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
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
