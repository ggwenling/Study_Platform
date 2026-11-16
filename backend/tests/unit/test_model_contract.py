"""Frozen contracts from database dictionary v2.0, independent of model builders."""

import re

import sqlalchemy as sa

from study_platform.persistence import models  # noqa: F401
from study_platform.persistence.base import Base

# Source dictionary section 3. Only users.status's width needs the documented correction.
FIELD_CONTRACT = """
users: id email password_hash display_name role status email_verified_at last_login_at created_at updated_at deleted_at
user_sessions: id user_id refresh_token_hash csrf_secret_hash user_agent_hash expires_at revoked_at created_at updated_at
courses: id owner_user_id name subject description language settings created_at updated_at deleted_at
materials: id owner_user_id course_id title kind status created_at updated_at deleted_at
material_versions: id material_id version_no is_current source_mime source_sha256 byte_size object_key page_count parser_profile parse_status failure_code created_at updated_at
material_sections: id material_version_id parent_section_id ordinal heading level text locator confidence created_at
index_versions: id material_version_id generation pipeline_profile embedding_profile embedding_dim status is_active chunk_count manifest_sha256 failure_code ready_at created_at updated_at
material_chunks: id index_version_id material_version_id section_id group_id ordinal text text_sha256 locator lexical created_at
chunk_embeddings: chunk_id model_profile_id embedding embedding_sha256 created_at
knowledge_points: id course_id parent_id canonical_name description subject_tag difficulty source confidence status created_at updated_at
knowledge_point_sources: id knowledge_point_id chunk_id material_version_id index_version_id evidence_span confidence created_at
source_questions: id material_version_id section_id ordinal question_type stem options reference_answer solution locator confidence created_at
conversations: id owner_user_id course_id project_knowledge_base_id title mode scope_snapshot created_at updated_at deleted_at
messages: id conversation_id role content status sequence_no model_profile prompt_version workflow_run_id usage created_at updated_at
message_citations: id message_id chunk_id material_version_id index_version_id locator quote_sha256 ordinal created_at
notes: id owner_user_id course_id title note_type content_md generated_content_md edit_revision edited_at status source_scope generator_profile prompt_version created_at updated_at deleted_at
note_citations: id note_id chunk_id material_version_id index_version_id locator ordinal created_at
quizzes: id owner_user_id course_id title status difficulty source_scope generator_profile prompt_version created_at updated_at deleted_at
quiz_items: id quiz_id knowledge_point_id ordinal item_type stem options answer_key rubric explanation difficulty max_score created_at
quiz_item_sources: id quiz_item_id chunk_id material_version_id index_version_id locator created_at
quiz_attempts: id quiz_id owner_user_id status started_at submitted_at graded_at total_score max_score created_at updated_at
quiz_responses: id attempt_id quiz_id quiz_item_id answer score is_correct feedback grader_profile graded_at created_at updated_at
wrong_questions: id owner_user_id quiz_item_id last_response_id wrong_count status last_wrong_at next_review_at created_at updated_at
flashcard_decks: id owner_user_id course_id title source_scope generator_profile created_at updated_at deleted_at
flashcards: id deck_id knowledge_point_id front back ease_factor interval_days repetition_count due_at status created_at updated_at
flashcard_sources: id flashcard_id chunk_id material_version_id index_version_id locator created_at
flashcard_reviews: id flashcard_id owner_user_id rating previous_state new_state reviewed_at idempotency_key
mastery_states: id owner_user_id knowledge_point_id mastery_score evidence_count estimator_version last_practiced_at next_review_at created_at updated_at
study_plans: id owner_user_id course_id title status start_date end_date generator_profile created_at updated_at
study_plan_items: id study_plan_id knowledge_point_id task_type title scheduled_for estimated_minutes status completed_at created_at updated_at
learning_events: id owner_user_id course_id event_type event_key occurred_at subject_type subject_id payload created_at
ai_jobs: id owner_user_id job_type resource_type resource_id status progress payload result attempt_count max_attempts fencing lease_token_hash lease_expires_at cancel_requested_at failure_code next_run_at created_at updated_at finished_at
ai_job_attempts: id job_id attempt_no worker_id fencing started_at finished_at outcome failure_code usage
ai_workflow_runs: id owner_user_id conversation_id job_id graph_name graph_version thread_id checkpoint_namespace last_checkpoint_id status source_scope last_node model_profile prompt_version usage failure_code started_at finished_at created_at updated_at
outbox_events: id job_id event_key topic payload available_at delivered_at dispatch_attempts last_error_code created_at
idempotency_keys: id user_id route_scope key request_hash response_status response_body resource_type resource_id expires_at created_at
audit_logs: id actor_user_id action target_type target_id request_id outcome metadata created_at
"""


def test_all_dictionary_tables_and_fields_are_present_without_extra_columns():
    expected = {}
    for line in FIELD_CONTRACT.strip().splitlines():
        table, fields = line.strip().split(": ")
        expected[f"study.{table}"] = set(fields.split())
    assert len(expected) == 37  # Frozen 0001 dictionary.
    expected["study.materials"].update({"knowledge_base_id", "topic", "tags"})
    expected["study.material_chunks"].add("retrieval_enabled")
    expected["study.ai_workflow_runs"].update(
        {"knowledge_base_id", "answer_mode", "lease_expires_at", "fencing"}
    )
    expected["study.knowledge_bases"] = {
        "id",
        "owner_user_id",
        "name",
        "description",
        "last_synced_at",
        "created_at",
        "updated_at",
        "deleted_at",
    }
    expected["study.google_identities"] = {"subject", "user_id"}
    expected["study.password_resets"] = {"token_hash", "user_id", "email_hash", "client_hash", "created_at", "used_at"}
    assert len(expected) == 40
    assert set(Base.metadata.tables) == set(expected)
    for name, fields in expected.items():
        assert set(Base.metadata.tables[name].columns.keys()) == fields, name


def test_primary_key_generation_and_timezone_contract():
    for table in Base.metadata.tables.values():
        if table.name == "chunk_embeddings":
            assert list(table.primary_key.columns.keys()) == ["chunk_id", "model_profile_id"]
            assert all(c.server_default is None for c in table.primary_key.columns)
        elif table.name in {"google_identities", "password_resets"}:
            expected_key = "subject" if table.name == "google_identities" else "token_hash"
            assert list(table.primary_key.columns.keys()) == [expected_key]
        else:
            assert list(table.primary_key.columns.keys()) == ["id"]
            assert isinstance(table.c.id.type, sa.Uuid)
            assert str(table.c.id.server_default.arg) == "gen_random_uuid()"
        for column in table.columns:
            if isinstance(column.type, sa.DateTime):
                assert column.type.timezone is True, (table.name, column.name)
            if column.name in ("created_at", "updated_at"):
                assert not column.nullable
                assert str(column.server_default.arg) == "now()"
            if column.name == "updated_at":
                assert column.onupdate is not None
    assert Base.metadata.tables["study.chunk_embeddings"].c.embedding.type.dim == 1024
    assert str(Base.metadata.tables["study.users"].c.role.server_default.arg) == "'student'"
    assert (
        str(Base.metadata.tables["study.index_versions"].c.embedding_dim.server_default.arg)
        == "1024"
    )


def test_all_enum_literals_fit_declared_column_lengths():
    checked = 0
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            if not isinstance(constraint, sa.CheckConstraint):
                continue
            match = re.fullmatch(r"(\w+) IN \((.+)\)", str(constraint.sqltext))
            if match is None:
                continue
            column = table.c[match[1]]
            if not isinstance(column.type, sa.String):
                continue
            literals = re.findall(r"'((?:[^']|'')*)'", match[2])
            assert literals, constraint.name
            assert column.type.length is not None
            assert max(len(value.replace("''", "'")) for value in literals) <= column.type.length, (
                table.name,
                column.name,
                literals,
            )
            checked += 1
    assert checked >= 25  # Prevent an empty/unrecognized constraint set from passing silently.
    users = Base.metadata.tables["study.users"]
    assert users.c.status.type.length == 24
    assert any(
        "pending_verification" in str(c.sqltext)
        for c in users.constraints
        if isinstance(c, sa.CheckConstraint)
    )


def test_foreign_keys_resolve_to_unique_keys_and_restrict_deletion():
    for table in Base.metadata.tables.values():
        for constraint in table.foreign_key_constraints:
            assert constraint.ondelete == "RESTRICT"
            assert constraint.name
            remote_columns = tuple(element.column for element in constraint.elements)
            target = remote_columns[0].table
            assert all(column.table is target for column in remote_columns)
            unique_keys = [tuple(target.primary_key.columns)] + [
                tuple(c.columns) for c in target.constraints if isinstance(c, sa.UniqueConstraint)
            ]
            assert remote_columns in unique_keys, constraint.name


def test_core_composite_keys_protect_owner_quiz_and_source_lineage():
    required = {
        "materials": ("course_id", "owner_user_id"),
        "material_chunks": ("index_version_id", "material_version_id"),
        "quiz_responses": ("attempt_id", "quiz_id"),
        "messages": ("workflow_run_id", "conversation_id"),
        "ai_workflow_runs": ("conversation_id", "owner_user_id"),
    }
    for table, columns in required.items():
        assert columns in {
            tuple(f.column_keys)
            for f in Base.metadata.tables[f"study.{table}"].foreign_key_constraints
        }
    for table in (
        "knowledge_point_sources",
        "message_citations",
        "note_citations",
        "quiz_item_sources",
        "flashcard_sources",
    ):
        assert ("chunk_id", "index_version_id", "material_version_id") in {
            tuple(f.column_keys)
            for f in Base.metadata.tables[f"study.{table}"].foreign_key_constraints
        }
