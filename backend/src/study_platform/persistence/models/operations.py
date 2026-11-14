"""Operations database models; constraints mirror the initial migration."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from study_platform.persistence.base import Base


class LearningEvent(Base):
    __tablename__ = "learning_events"
    __table_args__ = (
        sa.UniqueConstraint("event_key", name="uq_learning_events_event_key"),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name="ck_learning_events_payload_json"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["study.users.id"],
            name="fk_learning_events_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "owner_user_id"],
            ["study.courses.id", "study.courses.owner_user_id"],
            name="fk_learning_events_course_id_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_learning_events_owner_created",
            "owner_user_id",
            sa.text("created_at DESC"),
            unique=False,
        ),
        sa.Index(
            "ix_learning_events_owner_course_occurred",
            "owner_user_id",
            "course_id",
            sa.text("occurred_at DESC"),
            unique=False,
        ),
        sa.Index("ix_learning_events_type_occurred", "event_type", "occurred_at", unique=False),
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
    event_type = sa.Column("event_type", sa.String(50), nullable=False)
    event_key = sa.Column("event_key", sa.String(160), nullable=False)
    occurred_at = sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False)
    subject_type = sa.Column("subject_type", sa.String(50), nullable=False)
    subject_id = sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False)
    payload = sa.Column(
        "payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    created_at = sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


class AiJob(Base):
    __tablename__ = "ai_jobs"
    __table_args__ = (
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
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["study.users.id"],
            name="fk_ai_jobs_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_ai_jobs_owner_created", "owner_user_id", sa.text("created_at DESC"), unique=False
        ),
        sa.Index("ix_ai_jobs_status_next_run", "status", "next_run_at", unique=False),
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
    job_type = sa.Column("job_type", sa.String(40), nullable=False)
    resource_type = sa.Column("resource_type", sa.String(40), nullable=False)
    resource_id = sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False)
    status = sa.Column("status", sa.String(20), nullable=False)
    progress = sa.Column("progress", sa.SmallInteger(), nullable=False, server_default=sa.text("0"))
    payload = sa.Column(
        "payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    result = sa.Column("result", postgresql.JSONB(), nullable=True)
    attempt_count = sa.Column("attempt_count", sa.SmallInteger(), nullable=False)
    max_attempts = sa.Column("max_attempts", sa.SmallInteger(), nullable=False)
    fencing = sa.Column("fencing", sa.BigInteger(), nullable=False, server_default=sa.text("0"))
    lease_token_hash = sa.Column("lease_token_hash", sa.CHAR(64), nullable=True)
    lease_expires_at = sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    cancel_requested_at = sa.Column(
        "cancel_requested_at", sa.DateTime(timezone=True), nullable=True
    )
    failure_code = sa.Column("failure_code", sa.String(64), nullable=True)
    next_run_at = sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False)
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
    finished_at = sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True)


class AiJobAttempt(Base):
    __tablename__ = "ai_job_attempts"
    __table_args__ = (
        sa.UniqueConstraint("job_id", "attempt_no", name="uq_ai_job_attempts_job_id_attempt_no"),
        sa.CheckConstraint("attempt_no > 0", name="ck_ai_job_attempts_attempt_no_positive"),
        sa.CheckConstraint("fencing >= 0", name="ck_ai_job_attempts_fencing_nonnegative"),
        sa.CheckConstraint("jsonb_typeof(usage) = 'object'", name="ck_ai_job_attempts_usage_json"),
        sa.CheckConstraint(
            "outcome IN ('success', 'retry', 'failed', 'cancelled')",
            name="ck_ai_job_attempts_outcome_values",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["study.ai_jobs.id"],
            name="fk_ai_job_attempts_job_id",
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
    job_id = sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False)
    attempt_no = sa.Column("attempt_no", sa.SmallInteger(), nullable=False)
    worker_id = sa.Column("worker_id", sa.String(128), nullable=False)
    fencing = sa.Column("fencing", sa.BigInteger(), nullable=False)
    started_at = sa.Column("started_at", sa.DateTime(timezone=True), nullable=False)
    finished_at = sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True)
    outcome = sa.Column("outcome", sa.String(16), nullable=True)
    failure_code = sa.Column("failure_code", sa.String(64), nullable=True)
    usage = sa.Column("usage", postgresql.JSONB(), nullable=True)


class AiWorkflowRun(Base):
    __tablename__ = "ai_workflow_runs"
    __table_args__ = (
        sa.UniqueConstraint("id", "conversation_id", name="uq_ai_workflow_runs_id_conversation_id"),
        sa.CheckConstraint(
            "jsonb_typeof(source_scope) = 'object'", name="ck_ai_workflow_runs_source_scope_json"
        ),
        sa.CheckConstraint("answer_mode IN ('knowledge_only', 'with_general')", name="ck_ai_workflow_runs_answer_mode"),
        sa.ForeignKeyConstraint(["knowledge_base_id", "owner_user_id"],
            ["study.knowledge_bases.id", "study.knowledge_bases.owner_user_id"],
            name="fk_ai_workflow_runs_knowledge_base_owner", ondelete="RESTRICT", use_alter=True),
        sa.Index("ix_ai_workflow_runs_active_rag", "conversation_id", unique=True,
            postgresql_where=sa.text("status IN ('created', 'running') AND source_scope->>'intent' IN ('qa', 'summary')")),
        sa.CheckConstraint("jsonb_typeof(usage) = 'object'", name="ck_ai_workflow_runs_usage_json"),
        sa.CheckConstraint(
            "status IN ('created', 'running', 'awaiting_student', 'completed', 'failed', 'cancelled')",
            name="ck_ai_workflow_runs_status_values",
        ),
        sa.CheckConstraint(
            "(conversation_id IS NULL) <> (job_id IS NULL)",
            name="ck_ai_workflow_runs_single_subject",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["study.users.id"],
            name="fk_ai_workflow_runs_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "owner_user_id"],
            ["study.conversations.id", "study.conversations.owner_user_id"],
            name="fk_ai_workflow_runs_conversation_id_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            ["study.ai_jobs.id", "study.ai_jobs.owner_user_id"],
            name="fk_ai_workflow_runs_job_id_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_ai_workflow_runs_owner_created",
            "owner_user_id",
            sa.text("created_at DESC"),
            unique=False,
        ),
        sa.Index(
            "ix_ai_workflow_runs_conversation_created",
            "conversation_id",
            sa.text("created_at DESC"),
            unique=False,
        ),
        sa.Index("ix_ai_workflow_runs_job", "job_id", unique=False),
        sa.Index(
            "ix_ai_workflow_runs_thread_created",
            "thread_id",
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
    conversation_id = sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True)
    knowledge_base_id = sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=True)
    answer_mode = sa.Column("answer_mode", sa.String(24), nullable=True)
    lease_expires_at = sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    fencing = sa.Column("fencing", sa.BigInteger(), nullable=False, server_default=sa.text("0"))
    job_id = sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True)
    graph_name = sa.Column("graph_name", sa.String(64), nullable=True)
    graph_version = sa.Column("graph_version", sa.String(50), nullable=True)
    thread_id = sa.Column("thread_id", sa.String(128), nullable=True)
    checkpoint_namespace = sa.Column("checkpoint_namespace", sa.String(128), nullable=True)
    last_checkpoint_id = sa.Column("last_checkpoint_id", sa.String(256), nullable=True)
    status = sa.Column("status", sa.String(24), nullable=False)
    source_scope = sa.Column(
        "source_scope", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    last_node = sa.Column("last_node", sa.String(80), nullable=True)
    model_profile = sa.Column("model_profile", sa.String(128), nullable=True)
    prompt_version = sa.Column("prompt_version", sa.String(50), nullable=True)
    usage = sa.Column("usage", postgresql.JSONB(), nullable=True)
    failure_code = sa.Column("failure_code", sa.String(64), nullable=True)
    started_at = sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)
    finished_at = sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True)
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


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        sa.UniqueConstraint("event_key", name="uq_outbox_events_event_key"),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name="ck_outbox_events_payload_json"
        ),
        sa.CheckConstraint(
            "dispatch_attempts >= 0", name="ck_outbox_events_dispatch_attempts_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["study.ai_jobs.id"],
            name="fk_outbox_events_job_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_outbox_events_available",
            "available_at",
            unique=False,
            postgresql_where=sa.text("delivered_at IS NULL"),
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
    job_id = sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True)
    event_key = sa.Column("event_key", sa.String(160), nullable=False)
    topic = sa.Column("topic", sa.String(60), nullable=False)
    payload = sa.Column("payload", postgresql.JSONB(), nullable=False)
    available_at = sa.Column("available_at", sa.DateTime(timezone=True), nullable=False)
    delivered_at = sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True)
    dispatch_attempts = sa.Column(
        "dispatch_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
    )
    last_error_code = sa.Column("last_error_code", sa.String(64), nullable=True)
    created_at = sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        sa.UniqueConstraint(
            "user_id", "route_scope", "key", name="uq_idempotency_keys_user_id_route_scope_key"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(response_body) = 'object'", name="ck_idempotency_keys_response_body_json"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["study.users.id"],
            name="fk_idempotency_keys_user_id",
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
    user_id = sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False)
    route_scope = sa.Column("route_scope", sa.String(140), nullable=False)
    key = sa.Column("key", sa.String(128), nullable=False)
    request_hash = sa.Column("request_hash", sa.CHAR(64), nullable=False)
    response_status = sa.Column("response_status", sa.SmallInteger(), nullable=True)
    response_body = sa.Column("response_body", postgresql.JSONB(), nullable=True)
    resource_type = sa.Column("resource_type", sa.String(40), nullable=True)
    resource_id = sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True)
    expires_at = sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False)
    created_at = sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        sa.CheckConstraint(
            "jsonb_typeof(\"metadata\") = 'object'", name="ck_audit_logs_metadata_json"
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'denied', 'error')", name="ck_audit_logs_outcome_values"
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["study.users.id"],
            name="fk_audit_logs_actor_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_audit_logs_actor_created", "actor_user_id", sa.text("created_at DESC"), unique=False
        ),
        sa.Index(
            "ix_audit_logs_action_created", "action", sa.text("created_at DESC"), unique=False
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
    actor_user_id = sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True)
    action = sa.Column("action", sa.String(80), nullable=False)
    target_type = sa.Column("target_type", sa.String(60), nullable=False)
    target_id = sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True)
    request_id = sa.Column("request_id", sa.String(128), nullable=False)
    outcome = sa.Column("outcome", sa.String(16), nullable=False)
    metadata_ = sa.Column(
        "metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    created_at = sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )
