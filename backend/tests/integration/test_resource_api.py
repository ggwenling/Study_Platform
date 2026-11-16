"""Database-backed ownership, paging and soft-delete resource checks."""

import os
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from study_platform.api.v1.auth import router as auth_router
from study_platform.api.v1.resources import router
from study_platform.core.config import Settings
from study_platform.core.errors import ApiError, api_error_handler
from study_platform.persistence.database import get_db
from study_platform.persistence.models import AiWorkflowRun, Message

pytestmark = pytest.mark.integration


@pytest.fixture
def resource_client():
    if os.getenv("RUN_DB_TESTS") != "1" or not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("explicit PostgreSQL test database required")
    url = make_url(os.environ["TEST_DATABASE_URL"])
    if not (url.database or "").endswith("_test") or {"dbname", "service", "servicefile"} & set(
        url.query
    ):
        pytest.fail("isolated *_test database required")
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    with engine.connect() as connection:
        tx = connection.begin()
        db = Session(connection, join_transaction_mode="create_savepoint")
        app = FastAPI()
        app.state.settings = Settings(_env_file=None, auth_jwt_secret="resource-test-key" * 3)
        app.include_router(auth_router)
        app.include_router(router)
        app.add_exception_handler(ApiError, api_error_handler)
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app, headers={"Origin": "http://localhost:5173"}) as http:
            yield http, db
        db.close()
        tx.rollback()
    engine.dispose()


def signup(http):
    result = http.post(
        "/auth/register",
        json={
            "email": f"{uuid4().hex}@example.test",
            "password": "safe-password-123",
            "display_name": "Resource tester",
        },
    )
    assert result.status_code == 201
    http.headers["Authorization"] = "Bearer " + result.json()["access_token"]
    return UUID(result.json()["user"]["id"])


def test_project_conversation_ownership_paging_and_library_deletion(resource_client):
    http, _ = resource_client
    signup(http)
    kb = http.post("/knowledge-bases", json={"name": "Project"}).json()["id"]
    other = http.post("/knowledge-bases", json={"name": "Other"}).json()["id"]
    first = http.post("/conversations", json={"project_knowledge_base_id": kb}).json()
    second = http.post("/conversations", json={"project_knowledge_base_id": kb}).json()
    outside = http.post("/conversations", json={"project_knowledge_base_id": other}).json()
    params = {"project_knowledge_base_id": kb, "limit": 1}
    page = http.get("/conversations", params=params).json()
    assert len(page["items"]) == 1 and page["next_cursor"]
    assert page["items"][0]["project_knowledge_base_id"] == kb
    next_page = http.get("/conversations", params={**params, "cursor": page["next_cursor"]}).json()
    assert {page["items"][0]["id"], next_page["items"][0]["id"]} == {first["id"], second["id"]}
    assert http.get("/conversations", params={**params, "cursor": outside["id"]}).status_code == 400
    owner_headers = dict(http.headers)
    signup(http)
    assert http.post("/conversations", json={"project_knowledge_base_id": kb}).status_code == 404
    assert http.get("/conversations", params=params).status_code == 404
    http.headers.update(owner_headers)
    assert http.delete(f"/knowledge-bases/{kb}").status_code == 204
    assert http.get(f'/conversations/{first["id"]}').json()["project_knowledge_base_id"] is None


def test_resources_ownership_pagination_and_tombstones(resource_client):
    http, db = resource_client
    owner = signup(http)
    a = http.post("/knowledge-bases", json={"name": "Alpha"}).json()
    b = http.post("/knowledge-bases", json={"name": "Beta"}).json()
    page = http.get("/knowledge-bases", params={"limit": 1}).json()
    assert len(page["items"]) == 1 and page["next_cursor"]
    second = http.get("/knowledge-bases", params={"limit": 1, "cursor": page["next_cursor"]}).json()
    assert second["items"][0]["id"] != page["items"][0]["id"]
    conversation = http.post("/conversations", json={"title": "Alpha conversation"}).json()
    run = AiWorkflowRun(
        id=uuid4(),
        owner_user_id=owner,
        conversation_id=UUID(conversation["id"]),
        knowledge_base_id=UUID(a["id"]),
        status="running",
        fencing=4,
        source_scope={
            "intent": "qa",
            "knowledge_base_id": a["id"],
            "name_snapshot": "Alpha",
            "answer_mode": "knowledge_only",
            "index_version_ids": [],
        },
    )
    db.add(run)
    db.flush()
    message = Message(
        id=uuid4(),
        conversation_id=UUID(conversation["id"]),
        role="assistant",
        content="Partial",
        status="streaming",
        sequence_no=0,
        workflow_run_id=run.id,
    )
    db.add(message)
    db.commit()
    assert (
        http.get(f"/conversations/{conversation['id']}/messages").json()["items"][0]["scope"][
            "knowledge_base_name"
        ]
        == "Alpha"
    )
    assert len(http.get("/search", params={"q": "Alpha"}).json()["items"]) == 2
    assert http.delete(f"/conversations/{conversation['id']}").status_code == 204
    db.refresh(run)
    db.refresh(message)
    assert run.status == "cancelled" and run.fencing == 5
    assert message.status == "cancelled"
    assert http.get(f"/conversations/{conversation['id']}").status_code == 404
    assert http.delete(f"/knowledge-bases/{a['id']}").status_code == 204
    assert http.get(f"/knowledge-bases/{a['id']}").status_code == 404
    signup(http)
    assert http.get(f"/knowledge-bases/{b['id']}").status_code == 404
    assert http.get("/knowledge-bases").json()["items"] == []
    assert http.get("/dashboard/overview").json()["knowledge_base_count"] == 0
