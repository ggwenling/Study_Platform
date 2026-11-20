"""AI jobs enqueue immediately and publish results only through a valid lease."""

from contextlib import nullcontext
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_ingestion_db import state  # noqa: F401 - shared isolated ingestion fixture

from study_platform.api.v1 import ai_jobs as jobs_api
from study_platform.api.v1 import learning as learning_api
from study_platform.api.v1 import studio as studio_api
from study_platform.api.v1.auth import get_current_user
from study_platform.core.config import Settings
from study_platform.core.errors import ApiError, api_error_handler
from study_platform.persistence.database import get_db
from study_platform.persistence.models import AiJob, FlashcardDeck, KnowledgeBase, Note, Quiz
from study_platform.services import (
    ai_jobs,
    ingestion,
    job_queue,
    learning,
    model_connections,
    studio,
)
from study_platform.services.storage import source_path

pytestmark = pytest.mark.integration


@pytest.fixture
def queued_jobs(state, monkeypatch):  # noqa: F811
    factory, _, version_id, _, kb_id = state
    settings = Settings(_env_file=None, learning_stub_grader=True)
    monkeypatch.setattr(studio, "get_settings", lambda: settings)
    monkeypatch.setattr(learning, "get_settings", lambda: settings)
    monkeypatch.setattr(ai_jobs, "session_factory", lambda: factory)
    monkeypatch.setattr(model_connections, "task", lambda *args, **kwargs: nullcontext())
    source_path(version_id).write_text("向量检索是基于嵌入表示比较查询与文档语义相似度的方法。", encoding="utf-8")
    assert ingestion.run_once()
    with factory() as db:
        owner_id = db.get(KnowledgeBase, kb_id).owner_user_id

    def db_dependency():
        with factory() as db:
            yield db

    user = SimpleNamespace(id=owner_id)
    app = FastAPI()
    for router in (jobs_api.router, learning_api.router, studio_api.router):
        app.include_router(router)
    app.add_exception_handler(ApiError, api_error_handler)
    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        yield client, factory, kb_id, user


def run(claim, factory):
    with factory() as db:
        task = job_queue.claim(db, (claim,))
    assert task is not None
    ai_jobs.run(*task[:3])


@pytest.mark.parametrize(("kind", "template", "body", "model"), [
    ("notes", "/knowledge-bases/{kb}/notes/generate", {"kind": "study_guide"}, Note),
    ("flashcards", "/knowledge-bases/{kb}/flashcard-decks/generate", {"count": 1}, FlashcardDeck),
    ("quiz", "/learning/knowledge-bases/{kb}/quizzes/generate", {"count": 1}, Quiz),
])
def test_generation_is_queued_scoped_and_published(queued_jobs, kind, template, body, model):
    client, factory, kb_id, user = queued_jobs
    result = client.post(template.format(kb=kb_id), json=body)
    assert result.status_code == 202, result.text
    queued = result.json()
    assert queued["job_type"] == kind and queued["status"] == "queued"
    path = f"/ai-jobs/{queued['job_id']}"
    owner_id, user.id = user.id, uuid4()
    assert client.get(path).status_code == 404
    assert client.post(f"{path}/cancel").status_code == 404
    user.id = owner_id
    assert [row["id"] for row in client.get("/ai-jobs", params={"job_types": kind}).json()] == [queued["job_id"]]
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(model).where(model.owner_user_id == owner_id)) == 0
    run(kind, factory)
    finished = client.get(path).json()
    assert (finished["status"], finished["progress"], finished["phase"]) == ("succeeded", 100, "ready"), finished
    assert client.get("/ai-jobs", params={"job_types": kind}).json() == []
    with factory() as db:
        assert db.get(model, UUID(finished["result_resource_id"])).owner_user_id == owner_id


def test_cancellation_during_model_call_prevents_result_and_releases_slot(queued_jobs, monkeypatch):
    client, factory, kb_id, user = queued_jobs
    endpoint = f"/knowledge-bases/{kb_id}/notes/generate"
    body = {"kind": "study_guide"}
    jobs = [client.post(endpoint, json=body) for _ in range(3)]
    assert all(result.status_code == 202 for result in jobs)
    assert client.post(endpoint, json=body).json()["code"] == "too_many_active_jobs"
    with factory() as db:
        claimed = job_queue.claim(db, ("notes",))
    assert claimed is not None
    job_id = str(claimed[0])
    path = f"/ai-jobs/{job_id}"

    def fake_model(*args, **kwargs):
        assert client.get(path).json()["status"] == "running"
        assert client.post(f"{path}/cancel").json()["status"] == "cancelled"
        return SimpleNamespace(content="这份结果不得落库。[S1]")

    settings = Settings(_env_file=None, learning_stub_grader=False)
    monkeypatch.setattr(studio, "get_settings", lambda: settings)
    monkeypatch.setattr(studio, "llm_available", lambda owner: True)
    monkeypatch.setattr(studio, "invoke_chat", fake_model)
    ai_jobs.run(*claimed[:3])
    cancelled = client.get(path).json()
    assert cancelled["status"] == "cancelled" and cancelled["result_resource_id"] is None
    assert client.post(endpoint, json=body).status_code == 202
    with factory() as db:
        assert db.get(AiJob, UUID(job_id)).fencing == 2
        assert db.scalar(sa.select(sa.func.count()).select_from(Note).where(Note.owner_user_id == user.id)) == 0


def test_generation_failure_exposes_a_retryable_user_action_without_a_result(queued_jobs, monkeypatch):
    client, factory, kb_id, _ = queued_jobs
    settings = Settings(_env_file=None, learning_stub_grader=False)
    monkeypatch.setattr(studio, "get_settings", lambda: settings)
    monkeypatch.setattr(studio, "llm_available", lambda owner: True)

    def fail(*args, **kwargs):
        raise ApiError(502, "model_quota_unavailable", "请检查模型账户额度。")

    monkeypatch.setattr(studio, "invoke_chat", fail)
    queued = client.post(f"/knowledge-bases/{kb_id}/flashcard-decks/generate", json={}).json()
    run("flashcards", factory)
    status = client.get(f"/ai-jobs/{queued['job_id']}").json()
    assert status["status"] == "failed"
    assert status["error_code"] == "model_quota_unavailable"
    assert status["error_message"] == "请检查模型账户额度。"
    assert status["result_resource_id"] is None
