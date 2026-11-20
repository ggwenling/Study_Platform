"""Ingestion against an explicitly isolated PostgreSQL test database."""

import hashlib
import os
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from study_platform.api.v1 import library_detail, materials
from study_platform.api.v1.auth import get_current_user
from study_platform.core.errors import ApiError, api_error_handler
from study_platform.persistence.database import get_db
from study_platform.persistence.models.identity import Material, User
from study_platform.persistence.models.knowledge import KnowledgeBase
from study_platform.persistence.models.materials import (
    IndexVersion,
    MaterialChunk,
    MaterialSection,
    MaterialVersion,
)
from study_platform.persistence.models.model_connections import ModelConnection
from study_platform.persistence.models.operations import AiJob, AiJobAttempt
from study_platform.services import ingestion, job_queue, model_calls, model_connections, reindex
from study_platform.services.storage import source_path
from study_platform.services.web_clip import WebClip

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


@pytest.mark.parametrize("cancel", [False, True], ids=["retry", "cancel"])
def test_reindex_keeps_both_active_indexes_until_atomic_switch(state, monkeypatch, cancel):
    factory, _, first_version, material_id, kb_id = state
    monkeypatch.setattr(reindex, "session_factory", lambda: factory)
    monkeypatch.setattr(model_connections, "task", lambda *_, **__: nullcontext())
    monkeypatch.setattr(ingestion.classification, "enqueue", lambda *_: None)
    assert ingestion.run_once()
    with factory() as db:
        from study_platform.api.v1.materials import create_job

        kb = db.get(KnowledgeBase, kb_id)
        owner = db.get(User, kb.owner_user_id)
        material = Material(id=uuid4(), owner_user_id=owner.id, knowledge_base_id=kb.id,
                            title="Second", kind="other", status="active")
        second = MaterialVersion(id=uuid4(), material_id=material.id, version_no=1, is_current=True,
                                 source_mime="text/plain", source_sha256="b" * 64, byte_size=6,
                                 object_key=str(uuid4()), parse_status="pending")
        target = ModelConnection(id=uuid4(), owner_user_id=owner.id, kind="embedding", name="Next",
                                 status="ready", revision=1, versions={"1": {
                                     "base_url": "https://example.test/v1", "model": "vector-next",
                                 }})
        db.add_all([material, target])
        db.flush()
        db.add(second)
        db.flush()
        create_job(db, owner, second)
        db.commit()
        source = source_path(second.id)
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("second", encoding="utf-8")
        versions = [first_version, second.id]
        target_id = target.id
        owner_id = owner.id
    assert ingestion.run_once()
    with factory() as db:
        old_ids = set(db.scalars(sa.select(IndexVersion.id).where(
            IndexVersion.material_version_id.in_(versions), IndexVersion.is_active.is_(True),
        )))
        assert len(old_ids) == 2

    app = FastAPI()
    app.include_router(library_detail.router)
    app.add_exception_handler(ApiError, api_error_handler)

    def provide_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = provide_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=owner_id)
    prefix = f"/knowledge-bases/{kb_id}"
    calls = []
    with TestClient(app) as client:
        created = client.post(f"{prefix}/reindex", json={"embedding_connection_id": str(target_id)})
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        assert created.json()["total_materials"] == 2
        assert client.post(f"{prefix}/reindex", json={}).json()["code"] == "reindex_in_progress"

        def embed(texts, **_):
            calls.extend(texts)
            status = client.get(f"{prefix}/reindex-status").json()
            assert status["running"] == 1 and status["status"] == "running"
            with factory() as db:
                assert not db.get(KnowledgeBase, kb_id).embedding_connection_id
                assert old_ids == set(db.scalars(sa.select(IndexVersion.id).where(
                    IndexVersion.material_version_id.in_(versions), IndexVersion.is_active.is_(True),
                )))
            if len(calls) == 2:
                assert status["succeeded"] == 1
                if cancel:
                    response = client.post(f"{prefix}/reindex/cancel")
                    assert response.json()["status"] == "cancelled", response.text
                else:
                    raise ApiError(502, "embedding_unavailable", "test failure")
            return [[0.1] * 1024 for _ in texts]

        monkeypatch.setattr(reindex, "embed_texts", embed)

        def run():
            with factory() as db:
                claimed = job_queue.claim(db, ("reindex",))
            assert str(claimed[0]) == job_id
            assert ingestion.run_once(claimed)

        run()
        status = client.get(f"{prefix}/reindex-status").json()
        if cancel:
            assert status["status"] == "cancelled"
        else:
            assert (status["status"], status["succeeded"], status["failed"]) == ("failed", 1, 1)
            assert status["failures"][0]["error_code"] == "embedding_unavailable"
            assert client.post(f"{prefix}/reindex/retry-failed").status_code == 202
            run()
            assert len(calls) == 3 and calls[1] == calls[2]
            assert client.get(f"{prefix}/reindex-status").json()["status"] == "succeeded"
    with factory() as db:
        indexes = db.scalars(sa.select(IndexVersion).where(IndexVersion.material_version_id.in_(versions))).all()
        assert sum(row.is_active for row in indexes) == 2
        assert db.get(KnowledgeBase, kb_id).embedding_connection_id == (None if cancel else target_id)
        assert all(row.is_active is cancel for row in indexes if row.id in old_ids)
        assert all(row.status == "ready" and row.embedding_profile == f"connection:{target_id}:1024"
                   for row in indexes if row.id not in old_ids)
        assert len(indexes) == (2 if cancel else 4)
        assert len(db.scalars(sa.select(MaterialChunk.id).where(MaterialChunk.index_version_id.in_(old_ids))).all()) == 2
        assert db.get(Material, material_id).title == "Test"


def test_reindex_yields_to_pending_ingestion_and_includes_its_new_index(state, monkeypatch):
    factory, _, version_id, _, kb_id = state
    monkeypatch.setattr(reindex, "session_factory", lambda: factory)
    monkeypatch.setattr(model_connections, "task", lambda *_, **__: nullcontext())
    monkeypatch.setattr(reindex, "embed_texts", lambda texts, **_: [[0.1] * 1024 for _ in texts])
    with factory() as db:
        kb = db.get(KnowledgeBase, kb_id, with_for_update=True)
        submitted = reindex.enqueue(db, kb, None, True)
        assert submitted["total_materials"] == 0
        claimed = job_queue.claim(db, ("reindex",))
    assert ingestion.run_once(claimed)
    with factory() as db:
        waiting = db.get(AiJob, submitted["job_id"])
        assert waiting.status == "queued"
        assert waiting.result["phase"] == "waiting_for_ingestion"
        assert db.get(KnowledgeBase, kb_id).embedding_binding is None
        claimed = job_queue.claim(db, ("ingest",))
    assert ingestion.run_once(claimed)
    with factory() as db:
        db.get(AiJob, submitted["job_id"]).next_run_at = job_queue.now()
        db.commit()
        claimed = job_queue.claim(db, ("reindex",))
    assert ingestion.run_once(claimed)
    with factory() as db:
        finished = reindex.status(db.get(AiJob, submitted["job_id"]))
        assert (finished["status"], finished["total"], finished["succeeded"]) == ("succeeded", 1, 1)
        indexes = db.scalars(sa.select(IndexVersion).where(IndexVersion.material_version_id == version_id)
                            .order_by(IndexVersion.generation)).all()
        assert [(index.generation, index.status) for index in indexes] == [(1, "retired"), (2, "ready")]


def test_web_clip_persists_source_and_publishes_markdown(state, monkeypatch):
    factory, job_id, _, material_id, kb_id = state
    with factory() as db:
        owner = db.get(Material, material_id).owner_user_id
        db.get(AiJob, job_id).status = "cancelled"
        db.commit()
        url = "https://example.com/article"
        markdown = "## A heading\n\nA public page imported for research."
        monkeypatch.setattr(materials, "fetch_public_webpage", lambda _: WebClip("Article", markdown, url))
        app = FastAPI()
        app.include_router(materials.router)
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=owner)
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app) as http:
            accepted = http.post(f"/knowledge-bases/{kb_id}/materials/web-clip", json={"url": url})
            assert accepted.status_code == 202, accepted.text
            result = accepted.json()
            db.expire_all()
            assert source_path(result["version_id"]).read_text(encoding="utf-8") == markdown
            assert ingestion.run_once()
            db.expire_all()
            assert http.get(f"/jobs/{result['job_id']}").json()["status"] == "succeeded"
            item = http.get(f"/materials/{result['material_id']}").json()
            assert item["source_url"] == url and item["title"] == "Article"
            assert item["mime_type"] == "text/markdown" and item["status"] == "ready"


def test_vision_reparse_switches_version_only_after_publication(state, monkeypatch):
    factory, _, version_id, material_id, kb_id = state
    monkeypatch.setattr(ingestion.classification, "enqueue", lambda *_: None)
    monkeypatch.setattr(ingestion, "ocr_image", lambda _: [{"text": "local OCR"}])
    vision = {"id": str(uuid4()), "kind": "vision", "revision": 3}
    monkeypatch.setattr(model_connections, "vision_snapshot", lambda *_: vision)
    monkeypatch.setattr(model_connections, "task", lambda *_, **_kwargs: nullcontext())
    seen = []

    def invoke(_prompt, _image, _mime, *, context, saved):
        assert context.purpose == "vision_ingest" and saved == vision
        seen.append(saved)
        if len(seen) == 1:
            raise ApiError(504, "model_timeout", "fake timeout")
        return "视觉结果"

    monkeypatch.setattr(model_calls, "invoke_vision", invoke)
    path = source_path(version_id)
    with Image.new("RGB", (16, 16), "white") as image:
        image.save(path, format="PNG")
    expected = path.read_bytes()
    with factory() as db:
        old = db.get(MaterialVersion, version_id)
        old.source_mime, old.byte_size, old.source_sha256 = "image/png", len(expected), hashlib.sha256(expected).hexdigest()
        owner = db.get(Material, material_id).owner_user_id
        db.commit()
    assert ingestion.run_once() and not seen
    with factory() as db:
        app = FastAPI()
        app.include_router(materials.router)
        app.include_router(library_detail.router)
        app.add_exception_handler(ApiError, api_error_handler)
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=owner)
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app) as http:
            route = f"/materials/{material_id}/reparse"
            body = {"extraction_mode": "vlm"}
            response = http.post(route, json=body)
            assert response.status_code == 202, response.text
            assert http.post(route, json=body).status_code == 409
            overview = f"/knowledge-bases/{kb_id}/overview"
            assert http.get(overview).json()["processing_count"] == 1
            db.commit()
            assert ingestion.run_once()
            db.expire_all()
            assert db.get(MaterialVersion, version_id).is_current
            assert http.get(f"/jobs/{response.json()['job_id']}").json()["status"] == "failed"
            assert http.get(overview).json()["processing_count"] == 0
            db.commit()
            response = http.post(route, json=body)
            assert response.status_code == 202, response.text
            assert ingestion.run_once()
            db.expire_all()
            new_id = response.json()["version_id"]
            assert not db.get(MaterialVersion, version_id).is_current
            assert db.get(MaterialVersion, new_id).is_current
            assert path.read_bytes() == source_path(new_id).read_bytes() == expected
            assert db.scalar(sa.select(IndexVersion.status).where(
                IndexVersion.material_version_id == version_id,
            )) == "retired"
            sections = db.execute(
                sa.select(MaterialSection.text, MaterialSection.locator)
                .where(MaterialSection.material_version_id == new_id)
            ).all()
            assert sections == [("视觉结果", {"image": 1, "extraction": "vlm"})]


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


def test_worker_publishes_markdown_headings_and_locations(state):
    from study_platform.api.v1.library_detail import locations

    factory, _, version_id, material_id, kb_id = state
    md_text = "# 第一章 导论\n导论正文\n\n## 1.1 背景\n背景细节\n"
    with factory.begin() as db:
        version = db.get(MaterialVersion, version_id)
        version.source_mime = "text/markdown"
        version.byte_size = len(md_text.encode("utf-8"))
        version.source_sha256 = hashlib.sha256(md_text.encode("utf-8")).hexdigest()
        owner_id = db.get(Material, material_id).owner_user_id
    source_path(version_id).write_text(md_text, encoding="utf-8")

    assert ingestion.run_once()
    with factory() as db:
        user = SimpleNamespace(id=owner_id)
        rows = locations(kb_id, material_id, db=db, user=user)
        assert [(row["heading"], row["locator"]["start_line"]) for row in rows] == [
            ("第一章 导论", 1),
            ("1.1 背景", 4),
        ]
