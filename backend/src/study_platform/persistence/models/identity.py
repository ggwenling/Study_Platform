"""Identity database models; constraints mirror the initial migration."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from study_platform.persistence.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint("role IN ('student', 'admin')", name="ck_users_role_values"),
        sa.CheckConstraint(
            "status IN ('active', 'disabled', 'pending_verification')",
            name="ck_users_status_values",
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
    email = sa.Column("email", postgresql.CITEXT(), nullable=False)
    password_hash = sa.Column("password_hash", sa.String(255), nullable=False)
    display_name = sa.Column("display_name", sa.String(100), nullable=False)
    role = sa.Column("role", sa.String(16), nullable=False, server_default=sa.text("'student'"))
    status = sa.Column("status", sa.String(24), nullable=False, server_default=sa.text("'active'"))
    email_verified_at = sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True)
    last_login_at = sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True)
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


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        sa.UniqueConstraint("refresh_token_hash", name="uq_user_sessions_refresh_token_hash"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["study.users.id"],
            name="fk_user_sessions_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_user_sessions_user_expires",
            "user_id",
            sa.text("expires_at DESC"),
            unique=False,
            postgresql_where=sa.text("revoked_at IS NULL"),
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
    refresh_token_hash = sa.Column("refresh_token_hash", sa.CHAR(64), nullable=False)
    csrf_secret_hash = sa.Column("csrf_secret_hash", sa.CHAR(64), nullable=False)
    user_agent_hash = sa.Column("user_agent_hash", sa.CHAR(64), nullable=True)
    expires_at = sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False)
    revoked_at = sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True)
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


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (
        sa.UniqueConstraint("id", "owner_user_id", name="uq_courses_id_owner_user_id"),
        sa.CheckConstraint("jsonb_typeof(settings) = 'object'", name="ck_courses_settings_json"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["study.users.id"],
            name="fk_courses_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_courses_owner_created",
            "owner_user_id",
            sa.text("created_at DESC"),
            unique=False,
            postgresql_where=sa.text("deleted_at IS NULL"),
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
    name = sa.Column("name", sa.String(200), nullable=False)
    subject = sa.Column("subject", sa.String(80), nullable=True)
    description = sa.Column("description", sa.Text(), nullable=True)
    language = sa.Column(
        "language", sa.String(16), nullable=False, server_default=sa.text("'zh-CN'")
    )
    settings = sa.Column(
        "settings", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
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


class Material(Base):
    __tablename__ = "materials"
    __table_args__ = (
        sa.UniqueConstraint("id", "owner_user_id", name="uq_materials_id_owner_user_id"),
        sa.CheckConstraint(
            "kind IN ('notes', 'textbook', 'exam', 'worksheet', 'other')",
            name="ck_materials_kind_values",
        ),
        sa.CheckConstraint("status IN ('active', 'deleted')", name="ck_materials_status_values"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["study.users.id"],
            name="fk_materials_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "owner_user_id"],
            ["study.courses.id", "study.courses.owner_user_id"],
            name="fk_materials_course_id_owner_user_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.Index(
            "ix_materials_owner_created", "owner_user_id", sa.text("created_at DESC"), unique=False
        ),
        sa.Index(
            "ix_materials_course_created", "course_id", sa.text("created_at DESC"), unique=False
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
    title = sa.Column("title", sa.String(512), nullable=False)
    kind = sa.Column("kind", sa.String(24), nullable=False)
    status = sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'active'"))
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
