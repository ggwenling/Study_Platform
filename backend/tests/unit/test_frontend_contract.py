import json
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from study_platform.api.v1 import chat
from study_platform.api.v1.auth import get_current_user
from study_platform.core.config import Settings
from study_platform.main import create_app


def test_turn_response_contract_and_serialization(monkeypatch):
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[get_current_user] = lambda: type("User", (), {"id": uuid4()})()
    conversation_id, turn_id, kb_id = uuid4(), uuid4(), uuid4()
    payload = {
        "turn_id": turn_id, "conversation_id": conversation_id, "status": "completed",
        "scope": {"knowledge_base_id": kb_id, "knowledge_base_name": "Notes",
                  "answer_mode": "knowledge_only", "index_version_ids": []},
        "messages": [], "error_code": None,
    }
    monkeypatch.setattr(chat.chat, "turn", lambda *args: payload)
    response = TestClient(app).get(f"/conversations/{conversation_id}/turns/{turn_id}")
    assert response.status_code == 200
    assert response.json()["turn_id"] == str(turn_id)
    assert response.json()["scope"]["knowledge_base_name"] == "Notes"
    assert response.json()["error_code"] is None
    schema = app.openapi()
    assert schema["paths"]["/conversations/{conversation_id}/turns/{turn_id}/cancel"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/TurnDTO"
    assert "text/event-stream" in schema["paths"]["/chat/stream"]["post"]["responses"]["200"]["content"]


def test_exported_frontend_contract_matches_application():
    contract = Path(__file__).resolve().parents[3] / "contracts" / "frontend.openapi.json"
    assert json.loads(contract.read_text(encoding="utf-8")) == create_app(Settings(_env_file=None)).openapi()
