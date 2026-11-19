"""User connections against a controlled OpenAI-compatible HTTP server and PostgreSQL."""

import asyncio
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from study_platform.api.v1.auth import get_current_user
from study_platform.core.config import Settings
from study_platform.core.errors import ApiError
from study_platform.main import create_app
from study_platform.persistence.database import get_db
from study_platform.persistence.models import AiJob, KnowledgeBase, ModelConnection, User
from study_platform.services import chat, classification, ingestion, model_connections
from study_platform.services.learning_grade import grade_text_answer
from study_platform.services.learning_llm import (
    FragmentRef,
    generate_questions_with_llm,
    grade_with_llm,
)
from study_platform.services.model_calls import ainvoke_chat, invoke_chat
from study_platform.services.model_usage import ModelUsageContext
from study_platform.services.models import embed_texts
from study_platform.services.query_rewrite import rewrite_query

pytestmark = pytest.mark.integration


@pytest.fixture
def compatible_api():
    calls = []
    modes = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body, self.headers.get("Authorization")))
            model = modes.get(body["model"], body["model"])
            if model in {"unauthorized", "rate-limit", "timeout"}:
                if model == "timeout":
                    time.sleep(0.2)
                self.send_response(401 if model == "unauthorized" else 429)
                self.end_headers()
                try:
                    self.wfile.write(b'{"error":{"message":"fixture error"}}')
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            if model == "redirect":
                self.send_response(307)
                self.send_header("Location", "http://127.0.0.1:9/stolen")
                self.end_headers()
                return
            if self.path.endswith("/embeddings"):
                inputs = body["input"]
                vector = [0.2] * (512 if model == "wrong-dimension" else 1024)
                if model == "zero":
                    vector = [0] * 1024
                if model == "nan":
                    vector[0] = float("nan")
                data = [
                    {"object": "embedding", "index": i, "embedding": vector}
                    for i in range(len(inputs))
                ]
                if model == "missing":
                    data = data[:1]
                if model == "duplicate":
                    data[-1]["index"] = 0
                payload = {
                    "object": "list",
                    "model": model,
                    "data": data,
                    "usage": {"prompt_tokens": 2, "total_tokens": 2},
                }
                content_type = "application/json"
                result = json.dumps(payload)
            else:
                system = body["messages"][0]["content"]
                content = "OK"
                if "本轮资料JSON" in system:
                    content = "概念说明 [S1]"
                if "等义" in system:
                    content = json.dumps({"query": "如何理解重要概念"})
                elif "标签" in system:
                    content = json.dumps({"topic": "知识", "tags": ["概念"]})
                elif "source_indices" in system:
                    content = json.dumps(
                        {
                            "questions": [
                                {
                                    "stem": "概念是什么？",
                                    "reference_answer": "概念说明",
                                    "source_indices": [1],
                                }
                            ]
                        }
                    )
                elif "verdict" in system:
                    content = json.dumps(
                        {"verdict": "correct", "confidence": 0.98, "reason": "含义一致"}
                    )
                if body.get("stream"):
                    part = {
                        "id": "fixture",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": content},
                                "finish_reason": None,
                            }
                        ],
                    }
                    result = f"data: {json.dumps(part)}\n\ndata: [DONE]\n\n"
                    content_type = "text/event-stream"
                else:
                    result = json.dumps(
                        {
                            "id": "fixture",
                            "object": "chat.completion",
                            "created": 1,
                            "model": model,
                            "choices": [
                                {
                                    "index": 0,
                                    "message": {"role": "assistant", "content": content},
                                    "finish_reason": "stop",
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 2,
                                "completion_tokens": 2,
                                "total_tokens": 4,
                            },
                        }
                    )
                    content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(result.encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(
            url=f"http://127.0.0.1:{server.server_port}/v1", calls=calls, modes=modes
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def connections_env(compatible_api, monkeypatch, tmp_path):
    if os.getenv("RUN_DB_TESTS") != "1" or not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("explicit PostgreSQL test database required")
    url = make_url(os.environ["TEST_DATABASE_URL"])
    if not (url.database or "").endswith("_test"):
        pytest.fail("isolated *_test database required")
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    settings = Settings(
        _env_file=None,
        database_url=url.render_as_string(hide_password=False),
        auth_jwt_secret="model-connections-test-secret" * 2,
        storage_dir=tmp_path,
        model_connections_key=Fernet.generate_key().decode(),
        model_allowed_origins=[compatible_api.url],
        deepseek_api_key="",
        embedding_api_key="",
        chat_fallback_model="must-not-be-used",
        chat_fallback_api_key="",
        learning_stub_grader=False,
        rag_query_rewrite_enabled=False,
    )
    for module in (
        "services.model_connections",
        "services.model_network",
        "services.models",
        "services.model_calls",
        "services.learning_llm",
        "services.learning_grade",
        "services.learning",
        "services.chat",
        "services.storage",
        "api.v1.model_connections",
        "api.v1.resources",
    ):
        monkeypatch.setattr(f"study_platform.{module}.get_settings", lambda: settings)
    with engine.connect() as connection:
        transaction = connection.begin()
        factory = sessionmaker(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        for module in (
            "services.model_connections",
            "services.model_usage",
            "services.chat",
            "services.ingestion",
            "api.v1.model_connections",
        ):
            monkeypatch.setattr(f"study_platform.{module}.session_factory", lambda: factory)
        for module in (
            "services.model_connections",
            "services.models",
            "services.model_network",
            "services.model_calls",
            "services.learning_llm",
            "services.learning_grade",
            "services.learning",
            "services.storage",
            "services.ingestion",
            "api.v1.model_connections",
            "api.v1.resources",
            "api.v1.materials",
        ):
            monkeypatch.setattr(f"study_platform.{module}.get_settings", lambda: settings)
        owner = SimpleNamespace(id=uuid4())
        outsider = uuid4()
        with factory.begin() as db:
            db.add_all(
                [
                    User(
                        id=key,
                        email=f"{key}@test.example",
                        password_hash="unused",
                        display_name="Models",
                    )
                    for key in (owner.id, outsider)
                ]
            )
        app = create_app(settings)

        def database():
            with factory() as db:
                yield db

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = lambda: owner
        with TestClient(app, base_url="http://testserver/api/v1") as client:
            yield SimpleNamespace(
                client=client,
                factory=factory,
                owner=owner,
                outsider=outsider,
                settings=settings,
                provider=compatible_api,
            )
        transaction.rollback()
    engine.dispose()


def create(state, kind="llm", model="model-a", key="fixture-secret-abcd"):
    body = {
        "name": model,
        "kind": kind,
        "model": model,
        "base_url": state.provider.url,
        "api_key": key,
    }
    response = state.client.post("/me/model-connections", json=body)
    assert response.status_code == 201, response.text
    return response.json(), body


def activate(state, kind="llm", model="model-a"):
    row, body = create(state, kind, model)
    result = state.client.post(f"/me/model-connections/{row['id']}/test")
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "ready", result.text
    assert (
        state.client.put(
            f"/me/model-connections/defaults/{kind}", json={"connection_id": row["id"]}
        ).status_code
        == 204
    )
    return row, body


def test_encryption_ownership_defaults_and_revision(connections_env):
    state = connections_env
    row, body = create(state)
    assert row["key_suffix"] == "abcd" and row["status"] == "draft"
    assert body["api_key"] not in json.dumps(row)
    with state.factory() as db:
        saved = db.get(ModelConnection, UUID(row["id"]))
        assert body["api_key"] not in json.dumps(saved.versions)
        assert model_connections.decrypt(saved.versions["1"]["key"]) == body["api_key"]
    assert (
        state.client.put(
            "/me/model-connections/defaults/llm", json={"connection_id": row["id"]}
        ).status_code
        == 409
    )
    original = state.owner.id
    state.owner.id = state.outsider
    assert not state.client.get("/me/model-connections").json()["items"]
    for method, path, params in (
        ("patch", row["id"], {"json": body}),
        ("delete", row["id"], {}),
        ("post", row["id"] + "/test", {}),
        ("put", "defaults/llm", {"json": {"connection_id": row["id"]}}),
    ):
        assert (
            getattr(state.client, method)("/me/model-connections/" + path, **params).status_code
            == 404
        )
    state.owner.id = original
    assert state.client.post(f"/me/model-connections/{row['id']}/test").json()["status"] == "ready"
    assert (
        state.client.put(
            "/me/model-connections/defaults/llm", json={"connection_id": row["id"]}
        ).status_code
        == 204
    )
    assert (
        state.client.delete(f"/me/model-connections/{row['id']}").json()["code"]
        == "model_connection_default"
    )
    updated = state.client.patch(
        f"/me/model-connections/{row['id']}", json={**body, "api_key": "replacement-secret-efgh"}
    ).json()
    assert (
        updated["revision"] == 2
        and updated["status"] == "draft"
        and updated["key_suffix"] == "efgh"
    )
    assert state.client.get("/capabilities").status_code == 200
    state.settings.model_connections_key = SecretStr("")
    assert (
        state.client.post("/me/model-connections", json=body).json()["code"]
        == "model_encryption_unavailable"
    )


@pytest.mark.parametrize("kind", ["llm", "embedding"])
@pytest.mark.parametrize("empty_key", [None, "", "   "])
def test_address_change_requires_explicit_key(connections_env, kind, empty_key):
    state = connections_env
    row, body = create(state, kind)
    path = f"/me/model-connections/{row['id']}"
    before = state.client.get("/me/model-connections").json()["items"][0]
    response = state.client.patch(
        path, json={**body, "base_url": body["base_url"] + "/other", "api_key": empty_key}
    )
    assert response.status_code == 422 and response.json()["code"] == "model_key_required"
    assert state.client.get("/me/model-connections").json()["items"][0] == before

    same = state.client.patch(
        path, json={**body, "base_url": body["base_url"] + "/", "api_key": empty_key}
    )
    assert same.status_code == 200 and same.json()["revision"] == 1
    changed = state.client.patch(
        path, json={**body, "base_url": body["base_url"] + "/other"}
    )
    assert changed.status_code == 200 and changed.json()["revision"] == 2


def test_completed_chat_replay_after_connection_deleted(connections_env):
    state = connections_env
    llm, _ = activate(state)
    kb = state.client.post("/knowledge-bases", json={"name": "Replay"}).json()
    payload = {
        "knowledge_base_id": kb["id"], "conversation_id": None,
        "answer_mode": "knowledge_only", "content": "Question", "intent": "qa",
        "material_ids": None,
    }
    key = uuid4().hex
    run_id, fence, _ = chat.prepare(state.owner.id, payload, key)
    assert chat.checkpoint(run_id, fence, "资料不足", "completed", [])
    assert state.client.put(
        "/me/model-connections/defaults/llm", json={"connection_id": None}
    ).status_code == 204
    assert state.client.delete(f"/me/model-connections/{llm['id']}").status_code == 204
    call_count = len(state.provider.calls)
    assert chat.prepare(state.owner.id, payload, key) == (run_id, fence, True)

    async def collect():
        return [part async for part in chat.stream(state.owner.id, run_id, fence, replay=True)]

    frames = asyncio.run(collect())
    assert [frame.split("\n", 1)[0] for frame in frames] == [
        "event: meta", "event: delta", "event: sources", "event: done"
    ]
    assert "资料不足" in frames[1]
    assert len(state.provider.calls) == call_count


@pytest.mark.parametrize(
    ("kind", "model", "code"),
    [
        ("llm", "unauthorized", "model_auth_failed"),
        ("llm", "rate-limit", "model_quota_unavailable"),
        ("llm", "redirect", "model_service_error"),
        ("embedding", "wrong-dimension", "embedding_dimension_invalid"),
        ("embedding", "zero", "embedding_invalid"),
        ("embedding", "nan", "embedding_invalid"),
        ("embedding", "missing", "embedding_invalid"),
        ("embedding", "duplicate", "embedding_invalid"),
    ],
)
def test_real_probe_rejects_bad_credentials_and_malformed_vectors(
    connections_env, kind, model, code
):
    state = connections_env
    row, _ = create(state, kind, model)
    result = state.client.post(f"/me/model-connections/{row['id']}/test").json()
    assert result["status"] == "failed" and result["error_code"] == code
    assert (
        state.client.put(
            f"/me/model-connections/defaults/{kind}", json={"connection_id": row["id"]}
        ).status_code
        == 409
    )
    assert len(state.provider.calls) == 1  # No fallback or redirect, no automatic provider retries.


def test_task_default_switch_pins_all_llm_purposes_and_personal_errors(connections_env):
    state = connections_env
    a, _ = activate(state)
    b, _ = activate(state, model="model-b")
    state.client.put("/me/model-connections/defaults/llm", json={"connection_id": a["id"]})
    owner = state.owner.id
    with model_connections.task(owner):
        state.client.put("/me/model-connections/defaults/llm", json={"connection_id": b["id"]})
        assert (
            state.client.delete(f"/me/model-connections/{a['id']}").json()["code"]
            == "model_connection_busy"
        )
        invoke_chat([HumanMessage(content="hello")], context=ModelUsageContext(owner, "chat"))
        asyncio.run(
            ainvoke_chat(
                [HumanMessage(content="summary")], context=ModelUsageContext(owner, "chat")
            )
        )
        asyncio.run(rewrite_query("重要概念是什么", owner_user_id=owner))
        questions, _ = generate_questions_with_llm(
            [
                FragmentRef(
                    index=1,
                    chunk_id=uuid4(),
                    title="资料",
                    text="概念说明",
                    material_version_id=uuid4(),
                    index_version_id=uuid4(),
                    locator={},
                )
            ],
            1,
            owner_user_id=owner,
        )
        assert questions
        assert grade_with_llm("题", "参考", "回答", owner_user_id=owner).verdict == "correct"
        grade = grade_text_answer("回答", "参考", 1.0, owner_user_id=owner)
        assert grade[0] == "correct" and grade[3] == "learning_llm_v1"
        assert classification.suggest({"title": "资料"}, owner_user_id=owner)["topic"] == "知识"
        assert all(call[1]["model"] == "model-a" for call in state.provider.calls[2:])
    with model_connections.task(owner):
        invoke_chat([HumanMessage(content="hello")], context=ModelUsageContext(owner, "chat"))
    assert state.provider.calls[-1][1]["model"] == "model-b"
    usage = state.client.get("/me/model-usage").json()
    assert any(row["label"].startswith("个人 · model-a") for row in usage["by_connection"])
    state.provider.modes["model-b"] = "unauthorized"
    with model_connections.task(owner):
        with pytest.raises(ApiError, match="认证") as error:
            invoke_chat([HumanMessage(content="hello")], context=ModelUsageContext(owner, "chat"))
        assert error.value.code == "model_auth_failed"
        with pytest.raises(ApiError):
            grade_with_llm("题", "参考", "回答", owner_user_id=owner)
        with pytest.raises(ApiError):
            asyncio.run(rewrite_query("问题", owner_user_id=owner))
    assert state.provider.calls[-1][1]["model"] == "model-b"


def test_library_binding_persists_and_queued_ingestion_pins_revision(connections_env):
    state = connections_env
    activate(state)
    state.settings.embedding_api_key = SecretStr("platform-fixture-secret")
    state.settings.embedding_base_url = state.provider.url
    state.settings.embedding_model = "platform-embed"
    old = state.client.post("/knowledge-bases", json={"name": "旧平台库"}).json()
    a, body = activate(state, "embedding", "embed-a")
    first = state.client.post("/knowledge-bases", json={"name": "库 A"}).json()
    b, _ = activate(state, "embedding", "embed-b")
    second = state.client.post("/knowledge-bases", json={"name": "库 B"}).json()
    assert old["embedding_connection_id"] is None
    assert (
        first["embedding_connection_id"] == a["id"] and second["embedding_connection_id"] == b["id"]
    )
    assert (
        state.client.patch(
            f"/me/model-connections/{a['id']}", json={**body, "model": "another-model"}
        ).json()["code"]
        == "model_connection_bound"
    )
    conflict = state.client.delete(f"/me/model-connections/{a['id']}")
    assert conflict.status_code == 409 and "库 A" in conflict.json()["message"]
    upload = state.client.post(
        f"/knowledge-bases/{first['id']}/materials",
        files={
            "file": (
                "concept.txt",
                "概念是事物的共同属性。定义是概念的说明。".encode(),
                "text/plain",
            )
        },
    )
    assert upload.status_code == 202, upload.text
    pending = upload.json()
    with state.factory() as db:
        job = db.get(AiJob, UUID(pending["job_id"]))
        assert job.payload["embedding_connection"]["revision"] == 1
    replaced = state.client.patch(
        f"/me/model-connections/{a['id']}", json={**body, "api_key": "rotated-fixture-secret"}
    ).json()
    assert replaced["status"] == "draft"
    assert ingestion.run_once()
    assert state.provider.calls[-1][2] == "Bearer fixture-secret-abcd"
    with state.factory() as db:
        job = db.get(AiJob, UUID(pending["job_id"]))
        assert job.status == "succeeded", job.failure_code
    state.client.put("/me/model-connections/defaults/embedding", json={"connection_id": None})
    state.settings.embedding_api_key = SecretStr("")
    assert state.client.post(f"/me/model-connections/{a['id']}/test").json()["status"] == "ready"
    capability = state.client.get("/capabilities").json()
    assert capability["rag"] and capability["library_rag"][first["id"]]
    assert not capability["library_rag"][old["id"]]
    with model_connections.task(state.owner.id, kb_id=UUID(first["id"])):
        assert (
            model_connections.embedding_profile(state.owner.id)
            == first["embedding_binding"]["profile"]
        )
        embed_texts(["查询"], context=ModelUsageContext(state.owner.id, "embedding"))
    assert state.provider.calls[-1][1]["model"] == "embed-a"
    assert state.provider.calls[-1][2] == "Bearer rotated-fixture-secret"
    with model_connections.task(state.owner.id, kb_id=UUID(second["id"])):
        embed_texts(["查询"], context=ModelUsageContext(state.owner.id, "embedding"))
    assert state.provider.calls[-1][1]["model"] == "embed-b"
    with state.factory() as db:
        assert db.get(KnowledgeBase, UUID(old["id"])).embedding_binding["model"] == "platform-embed"


def test_timeout_and_short_key_never_exposed(connections_env):
    state = connections_env
    row, _ = create(state, "embedding", "timeout", key="abc")
    assert row["key_suffix"] != "abc"
    state.settings.model_timeout_seconds = 0.02
    result = state.client.post(f"/me/model-connections/{row['id']}/test").json()
    assert result["error_code"] == "model_timeout"
    assert "abc" not in state.client.get("/me/model-connections").text


@pytest.mark.parametrize("llm_model", ["llm-one", "llm-two"])
def test_end_to_end_chat_summary_learning_and_failure_preserve_indexes(connections_env, llm_model):
    state = connections_env
    llm, llm_body = activate(state, model=llm_model)
    embedding, embedding_body = activate(state, "embedding", "embed-bound")
    state.settings.rag_query_rewrite_enabled = True
    kb = state.client.post("/knowledge-bases", json={"name": "问答测试"}).json()
    upload = state.client.post(
        f"/knowledge-bases/{kb['id']}/materials",
        files={"file": ("facts.txt", "概念是共同属性，定义说明概念。".encode(), "text/plain")},
    ).json()
    assert ingestion.run_once()
    assert ingestion.run_once()  # Queued classification uses its persisted LLM snapshot.
    for intent in ("qa", "summary"):
        payload = {
            "knowledge_base_id": kb["id"],
            "conversation_id": None,
            "answer_mode": "knowledge_only",
            "content": "概念是什么",
            "intent": intent,
            "material_ids": [upload["material_id"]] if intent == "summary" else None,
        }
        run_id, fence, replay = chat.prepare(state.owner.id, payload, uuid4().hex)

        async def collect():
            return "".join(
                [part async for part in chat.stream(state.owner.id, run_id, fence, replay)]
            )

        events = asyncio.run(collect())
        assert "event: done" in events, events
    result = state.client.post(
        f"/learning/knowledge-bases/{kb['id']}/quizzes/generate", json={"count": 1}
    )
    assert result.status_code == 200 and result.json()["item_count"] == 1, result.text
    assert any(
        "source_indices" in call[1]["messages"][0]["content"]
        for call in state.provider.calls if call[0].endswith("chat/completions")
    )
    assert all(
        call[1]["model"] == llm_model
        for call in state.provider.calls
        if call[0].endswith("chat/completions")
    )
    with state.factory() as db:
        from study_platform.persistence.models import IndexVersion

        ready_id = db.scalar(sa.select(IndexVersion.id).where(IndexVersion.is_active.is_(True)))
    # A failed key replacement blocks new work without retiring the usable index.
    state.client.patch(
        f"/me/model-connections/{embedding['id']}",
        json={**embedding_body, "api_key": "wrong-replacement-key"},
    )
    state.provider.modes["embed-bound"] = "unauthorized"
    assert (
        state.client.post(f"/me/model-connections/{embedding['id']}/test").json()["status"]
        == "failed"
    )
    with state.factory() as db:
        assert db.get(IndexVersion, ready_id).is_active
    state.provider.modes.clear()
    state.client.patch(f"/me/model-connections/{embedding['id']}", json=embedding_body)
    assert (
        state.client.post(f"/me/model-connections/{embedding['id']}/test").json()["status"]
        == "ready"
    )
    # A newly invalid LLM default must not prevent vector publication.
    state.client.patch(
        f"/me/model-connections/{llm['id']}",
        json={**llm_body, "api_key": "new-llm-key-not-yet-tested"},
    )
    extra = state.client.post(
        f"/knowledge-bases/{kb['id']}/materials",
        files={"file": ("more.txt", b"more facts", "text/plain")},
    ).json()
    assert ingestion.run_once()
    with state.factory() as db:
        assert db.get(AiJob, UUID(extra["job_id"])).status == "succeeded"
        failure = db.scalar(
            sa.select(AiJob).where(AiJob.job_type == "classification", AiJob.status == "failed")
        )
        assert failure.failure_code == "model_test_required"
