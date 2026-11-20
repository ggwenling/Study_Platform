import pytest
from fastapi import FastAPI

from study_platform.api.v1.materials import router


def test_material_openapi_exposes_response_contracts():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    schema = app.openapi()
    paths = schema["paths"]
    for path, method, status, model in [
        ("/api/v1/knowledge-bases/{kb_id}/materials", "post", "202", "UploadResponse"),
        ("/api/v1/knowledge-bases/{kb_id}/materials/web-clip", "post", "202", "UploadResponse"),
        ("/api/v1/knowledge-bases/{kb_id}/materials", "get", "200", "MaterialPage"),
        ("/api/v1/materials/{material_id}", "get", "200", "MaterialResponse"),
        ("/api/v1/materials/{material_id}/retry", "post", "202", "UploadResponse"),
        ("/api/v1/jobs/{job_id}", "get", "200", "JobResponse"),
        ("/api/v1/jobs/{job_id}/cancel", "post", "200", "JobCancelResponse"),
        ("/api/v1/material-versions/{version_id}/preview", "get", "200", "MaterialPreviewResponse"),
    ]:
        response_schema = paths[path][method]["responses"][status]["content"]["application/json"]["schema"]
        assert response_schema["$ref"] == f"#/components/schemas/{model}"
    material = schema["components"]["schemas"]["MaterialResponse"]
    assert "source_url" in material["properties"]
    assert set(material["required"]) == {
        "id", "knowledge_base_id", "title", "mime_type", "byte_size", "status", "version_id",
        "job_id", "created_at", "error_code", "error_message",
    }


def test_material_response_serializes_uuid_dates_and_nullable_errors(monkeypatch):
    from datetime import UTC, datetime
    from uuid import uuid4

    from fastapi.testclient import TestClient

    from study_platform.api.v1 import materials
    from study_platform.api.v1.auth import get_current_user
    from study_platform.persistence.database import get_db

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: object()
    app.dependency_overrides[get_db] = lambda: object()
    material_id, kb_id, version_id, job_id = (uuid4() for _ in range(4))
    monkeypatch.setattr(materials, "owned_material", lambda db, user, value: value)
    monkeypatch.setattr(materials, "dto", lambda db, material: {
        "id": material, "knowledge_base_id": kb_id, "title": "notes.txt",
        "mime_type": "text/plain", "byte_size": 3, "status": "ready",
        "version_id": version_id, "job_id": job_id,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "error_code": None, "error_message": None, "source_url": None,
    })
    response = TestClient(app).get(f"/materials/{material_id}")
    assert response.status_code == 200
    assert response.json() == {
        "id": str(material_id), "knowledge_base_id": str(kb_id), "title": "notes.txt",
        "mime_type": "text/plain", "byte_size": 3, "status": "ready",
        "version_id": str(version_id), "job_id": str(job_id),
        "created_at": "2026-01-01T00:00:00Z", "error_code": None, "error_message": None,
        "source_url": None, "page_count": None,
    }


@pytest.mark.parametrize("code,state,expected", [
    ("no_text_extracted", "failed", "未识别到可用文字"),
    ("storage_unavailable", "failed", "磁盘空间和目录权限"),
    ("model_auth_failed", "failed", "模型认证或权限不足"),
    ("model_quota_unavailable", "failed", "显式重试"),
    ("database_unavailable", "queued", "等待自动重试"),
])
def test_failure_reason_distinguishes_user_recovery_actions(monkeypatch, code, state, expected):
    from types import SimpleNamespace

    from study_platform.api.v1 import materials

    material = SimpleNamespace(
        id="m", knowledge_base_id="k", title="t", source_url=None, created_at=None
    )
    version = SimpleNamespace(id="v", source_mime="text/plain", byte_size=1, page_count=None)
    job = SimpleNamespace(id="j", status=state, result={}, failure_code=code)
    monkeypatch.setattr(materials, "latest_version", lambda *_: version)
    monkeypatch.setattr(materials, "latest_job", lambda *_: job)
    monkeypatch.setattr(materials, "pending_reparse_job", lambda *_: None)
    result = materials.dto(None, material)
    assert expected in result["error_message"]
    assert result["status"] == state
