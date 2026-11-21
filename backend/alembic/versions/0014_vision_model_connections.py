"""Add vision model connections and call purposes."""

import sqlalchemy as sa

from alembic import op

revision = "0014_vision_model_connections"
down_revision = "0013_material_source_url"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_model_connections_kind", "model_connections", schema="study")
    op.create_check_constraint(
        "ck_model_connections_kind",
        "model_connections",
        "kind IN ('llm', 'embedding', 'vision')",
        schema="study",
    )
    op.drop_constraint(
        "ck_model_call_records_purpose_values", "model_call_records", schema="study"
    )
    op.create_check_constraint(
        "ck_model_call_records_purpose_values",
        "model_call_records",
        "purpose IN ('chat', 'learning_quiz', 'learning_grade', 'query_rewrite', "
        "'embedding', 'classification', 'connection_test', 'vision_ingest', 'vision_grade')",
        schema="study",
    )


def downgrade():
    bind = op.get_bind()
    has_vision_connections = bind.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM study.model_connections WHERE kind='vision')")
    )
    has_vision_calls = bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM study.model_call_records "
            "WHERE purpose IN ('vision_ingest', 'vision_grade'))"
        )
    )
    if has_vision_connections or has_vision_calls:
        raise RuntimeError("Cannot downgrade while vision connections or usage records exist")

    op.drop_constraint("ck_model_call_records_purpose_values", "model_call_records", schema="study")
    op.create_check_constraint(
        "ck_model_call_records_purpose_values",
        "model_call_records",
        "purpose IN ('chat', 'learning_quiz', 'learning_grade', 'query_rewrite', "
        "'embedding', 'classification', 'connection_test')",
        schema="study",
    )
    op.drop_constraint("ck_model_connections_kind", "model_connections", schema="study")
    op.create_check_constraint(
        "ck_model_connections_kind",
        "model_connections",
        "kind IN ('llm', 'embedding')",
        schema="study",
    )
