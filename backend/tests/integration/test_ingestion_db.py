"""Ingestion against an explicitly isolated PostgreSQL test database."""

import hashlib
import os
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from study_platform.persistence.models.identity import Material, User
from study_platform.persistence.models.knowledge import KnowledgeBase
from study_platform.persistence.models.materials import (
    IndexVersion,
    MaterialSection,
    MaterialVersion,
)
from study_platform.persistence.models.operations import AiJob, AiJobAttempt
from study_platform.services import ingestion
from study_platform.services.storage import source_path

pytestmark = pytest.mark.integration


@pytest.fixture
def state(tmp_path, monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if os.getenv("RUN_DB_TESTS") != "1" or not url:
        pytest.skip("explicit test database required")
    url = make_url(url)
    if (
        url.get_backend_name() != "postgresql"
        or not (url.database or "").endswith("_test")
        or {"dbname", "service", "servicefile"} & set(url.query)
    ):
        pytest.fail("isolated PostgreSQL test database required")
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    with engine.connect() as connection:
        transaction = connection.begin()
        factory = sessionmaker(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        monkeypatch.setattr(ingestion, "session_factory", lambda: factory)
        from study_platform.core.config import get_settings

        settings = get_settings().model_copy(update={"storage_dir": tmp_path})
        monkeypatch.setattr(ingestion, "get_settings", lambda: settings)
        monkeypatch.setattr("study_platform.services.storage.get_settings", lambda: settings)
        monkeypatch.setattr(
            ingestion, "embed_texts", lambda texts, **_: [[0.1] * 1024 for _ in texts]
        )
        with factory() as db:
            user = User(
                id=uuid4(), email=f"{uuid4()}@example.test", password_hash="x", display_name="Test"
            )
            kb = KnowledgeBase(id=uuid4(), owner_user_id=user.id, name="Test")
            material = Material(
                id=uuid4(),
                owner_user_id=user.id,
                knowledge_base_id=kb.id,
                title="Test",
                kind="other",
                status="active",
            )
            version = MaterialVersion(
                id=uuid4(),
                material_id=material.id,
                version_no=1,
                is_current=True,
                source_mime="text/plain",
                source_sha256=hashlib.sha256(b"hello").hexdigest(),
                byte_size=5,
                object_key=str(uuid4()),
                parse_status="pending",
            )
            job = AiJob(
                id=uuid4(),
                owner_user_id=user.id,
                job_type="ingest",
                resource_type="material_version",
                resource_id=version.id,
                status="queued",
                progress=0,
                payload={},
                attempt_count=0,
                max_attempts=3,
                fencing=0,
                next_run_at=ingestion.now(),
            )
            for row in (user, kb, material, version, job):
                db.add(row)
                db.flush()
            db.commit()
            source = source_path(version.id)
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("hello", encoding="utf-8")
            yield factory, job.id, version.id, material.id, kb.id
        transaction.rollback()
    engine.dispose()


def test_worker_publishes_text_and_vector(state):
    factory, job_id, version_id, _, _ = state
    assert ingestion.run_once()
    with factory() as db:
        job = db.get(AiJob, job_id)
        assert job.status == "succeeded"
        assert db.get(MaterialVersion, version_id).parse_status == "ready"
        assert db.scalar(
            sa.select(IndexVersion).where(IndexVersion.material_version_id == version_id)
        ).is_active
        assert (
            db.scalar(
                sa.select(MaterialSection.text).where(
                    MaterialSection.material_version_id == version_id
                )
            )
            == "hello"
        )


def test_staging_permission_failure_marks_job_failed_without_killing_worker(state, monkeypatch):
    factory, job_id, version_id, _, _ = state
    from pathlib import Path

    mkdir = Path.mkdir

    def fail_staging(path, *args, **kwargs):
        if path.name.startswith("attempt-"):
            raise PermissionError("staging denied")
        return mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_staging)
    assert ingestion.run_once()
    with factory() as db:
        assert db.get(AiJob, job_id).status == "failed"
        assert db.get(AiJob, job_id).failure_code == "storage_unavailable"
        assert db.get(MaterialVersion, version_id).parse_status == "failed"
        assert not db.scalar(sa.select(IndexVersion).where(
            IndexVersion.material_version_id == version_id))
    assert ingestion.run_once() is False


def test_short_database_fault_during_indexing_retries_without_duplicate_publication(state, monkeypatch):
    factory, job_id, version_id, _, _ = state
    embed = ingestion.embed_texts

    def disconnect(_, **__):
        raise OperationalError("", {}, SimpleNamespace(sqlstate="08006"))

    monkeypatch.setattr(ingestion, "embed_texts", disconnect)
    assert ingestion.run_once()
    with factory() as db:
        job = db.get(AiJob, job_id)
        assert job.status == "queued"
        job.next_run_at = ingestion.now() - timedelta(seconds=1)
        db.commit()
    monkeypatch.setattr(ingestion, "embed_texts", embed)
    assert ingestion.run_once()
    with factory() as db:
        assert db.get(AiJob, job_id).status == "succeeded"
        assert db.scalar(sa.select(sa.func.count()).select_from(IndexVersion).where(
            IndexVersion.material_version_id == version_id, IndexVersion.is_active.is_(True))) == 1
        attempts = db.scalars(sa.select(AiJobAttempt).where(AiJobAttempt.job_id == job_id)
                             .order_by(AiJobAttempt.attempt_no)).all()
        assert [attempt.outcome for attempt in attempts] == ["retry", "success"]


def test_reclaimed_leases_fence_and_stop_after_three_attempts(state):
    factory, job_id, _, _, _ = state
    fences = []
    for _ in range(3):
        with factory() as db:
            claimed = ingestion.claim(db)
            fences.append(claimed[2])
            job = db.get(AiJob, job_id)
            job.lease_expires_at = ingestion.now() - timedelta(seconds=1)
            db.commit()
    assert fences == [1, 2, 3]
    with factory() as db:
        assert ingestion.claim(db) is None
        job = db.get(AiJob, job_id)
        assert job.status == "failed"
        attempts = db.scalars(
            sa.select(AiJobAttempt)
            .where(AiJobAttempt.job_id == job_id)
            .order_by(AiJobAttempt.attempt_no)
        ).all()
        assert [a.outcome for a in attempts] == ["retry", "retry", "failed"]
        with pytest.raises(ingestion.LostLease):
            ingestion.locked_job(db, job_id, fences[0])


def test_soft_deleted_knowledge_base_blocks_publish(state):
    factory, job_id, _, _, kb_id = state
    with factory() as db:
        claimed = ingestion.claim(db)
        kb = db.get(KnowledgeBase, kb_id)
        kb.deleted_at = ingestion.now()
        db.commit()
        with pytest.raises(ingestion.LostLease):
            ingestion.locked_job(db, job_id, claimed[2])


def test_cancellation_during_embedding_prevents_publication(state, monkeypatch):
    factory, job_id, version_id, _, _ = state

    def cancel_before_return(texts, **_):
        with factory() as db:
            job = db.get(AiJob, job_id)
            job.status = "cancelled"
            job.cancel_requested_at = ingestion.now()
            job.fencing += 1
            db.commit()
        return [[0.1] * 1024 for _ in texts]

    monkeypatch.setattr(ingestion, "embed_texts", cancel_before_return)
    assert ingestion.run_once()
    with factory() as db:
        assert db.get(AiJob, job_id).status == "cancelled"
        assert not db.scalar(
            sa.select(IndexVersion.id).where(
                IndexVersion.material_version_id == version_id, IndexVersion.is_active.is_(True)
            )
        )
        assert not db.scalar(
            sa.select(MaterialSection.id).where(MaterialSection.material_version_id == version_id)
        )


def test_soft_delete_during_embedding_prevents_publication(state, monkeypatch):
    factory, job_id, version_id, material_id, _ = state

    def delete_before_return(texts, **_):
        with factory() as db:
            material = db.get(Material, material_id)
            material.deleted_at = ingestion.now()
            material.status = "deleted"
            db.commit()
        return [[0.1] * 1024 for _ in texts]

    monkeypatch.setattr(ingestion, "embed_texts", delete_before_return)
    assert ingestion.run_once()
    with factory() as db:
        assert db.get(AiJob, job_id).status != "succeeded"
        assert not db.scalar(
            sa.select(IndexVersion.id).where(
                IndexVersion.material_version_id == version_id, IndexVersion.is_active.is_(True)
            )
        )


def test_authenticated_upload_persists_original_and_job(state):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from study_platform.api.v1.auth import get_current_user
    from study_platform.api.v1.materials import router
    from study_platform.persistence.database import get_db

    factory, _, _, _, kb_id = state
    with factory() as db:
        user = db.get(User, db.get(KnowledgeBase, kb_id).owner_user_id)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: user
        with TestClient(app, headers={"Origin": "http://localhost:5173"}) as client:
            response = client.post(
                f"/knowledge-bases/{kb_id}/materials",
                files={"file": ("sample.txt", b"uploaded", "text/plain")},
            )
        assert response.status_code == 202
        body = response.json()
        version_id = __import__("uuid").UUID(body["version_id"])
        assert source_path(version_id).read_bytes() == b"uploaded"
        assert db.get(MaterialVersion, version_id).material_id == __import__("uuid").UUID(
            body["material_id"]
        )
        assert db.get(AiJob, __import__("uuid").UUID(body["job_id"])).status == "queued"


def test_nonretryable_model_error_stops_after_one_attempt(state, monkeypatch):
    from study_platform.core.errors import ApiError

    factory, job_id, version_id, _, _ = state

    def quota_error(_, **__):
        raise ApiError(503, "model_quota", "Model quota unavailable", retryable=False)

    monkeypatch.setattr(ingestion, "embed_texts", quota_error)
    assert ingestion.run_once()
    with factory() as db:
        job = db.get(AiJob, job_id)
        assert (job.status, job.attempt_count, job.failure_code) == ("failed", 1, "model_quota")
        assert not db.scalar(
            sa.select(IndexVersion.id).where(
                IndexVersion.material_version_id == version_id, IndexVersion.is_active.is_(True)
            )
        )


def test_legacy_material_without_job_or_version_can_be_listed(state):
    from study_platform.api.v1.materials import dto

    factory, job_id, version_id, _, _ = state
    with factory.begin() as db:
        version = db.get(MaterialVersion, version_id)
        material = db.get(Material, version.material_id)
        version.parse_status = "ready"
        db.delete(db.get(AiJob, job_id))
        db.flush()
        saved = dto(db, material)
        assert (saved["status"], saved["job_id"], saved["version_id"]) == ("ready", None, version_id)
        db.delete(version)
        db.flush()
        saved = dto(db, material)
        assert (saved["status"], saved["version_id"], saved["error_code"]) == (
            "failed", None, "source_unavailable",
        )

def test_classification_runs_after_ingestion_without_applying_suggestions(state, monkeypatch):
    factory, _, version_id, material_id, _ = state
    assert ingestion.run_once()
    calls = []
    def suggestion(payload, *, owner_user_id=None):
        calls.append(payload)
        return {"topic": "Test topic", "tags": ["Test tag"]}
    monkeypatch.setattr(ingestion.classification, "suggest", suggestion)
    assert ingestion.run_once()
    assert len(calls) == 1 and calls[0]["excerpts"] == ["hello"]
    with factory() as db:
        job = db.scalar(sa.select(AiJob).where(
            AiJob.resource_id == version_id, AiJob.job_type == "classification"))
        assert job.status == "succeeded"
        assert job.result["suggestion"]["topic"] == "Test topic"
        material = db.get(Material, material_id)
        assert material.topic is None and material.tags == []
