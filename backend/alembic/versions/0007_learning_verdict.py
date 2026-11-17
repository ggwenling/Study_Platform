"""Learning verdict fields on quiz responses (no score semantics in API)."""

import sqlalchemy as sa

from alembic import op

revision = "0007_learning_verdict"
down_revision = "0006_security_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "quiz_responses",
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        schema="study",
    )
    op.add_column(
        "quiz_responses",
        sa.Column("verdict", sa.String(16), nullable=True),
        schema="study",
    )
    op.add_column(
        "quiz_responses",
        sa.Column("verdict_source", sa.String(16), nullable=True),
        schema="study",
    )
    op.add_column(
        "quiz_responses",
        sa.Column("reference_answer_effective", sa.Text(), nullable=True),
        schema="study",
    )
    op.create_check_constraint(
        "ck_quiz_responses_verdict_values",
        "quiz_responses",
        "verdict IS NULL OR verdict IN ('correct', 'incorrect', 'pending')",
        schema="study",
    )
    op.create_check_constraint(
        "ck_quiz_responses_verdict_source_values",
        "quiz_responses",
        "verdict_source IS NULL OR verdict_source IN ('auto', 'user')",
        schema="study",
    )
    op.create_check_constraint(
        "ck_quiz_responses_confidence_range",
        "quiz_responses",
        "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
        schema="study",
    )


def downgrade() -> None:
    op.drop_constraint("ck_quiz_responses_confidence_range", "quiz_responses", schema="study", type_="check")
    op.drop_constraint("ck_quiz_responses_verdict_source_values", "quiz_responses", schema="study", type_="check")
    op.drop_constraint("ck_quiz_responses_verdict_values", "quiz_responses", schema="study", type_="check")
    for col in ("reference_answer_effective", "verdict_source", "verdict", "confidence"):
        op.drop_column("quiz_responses", col, schema="study")