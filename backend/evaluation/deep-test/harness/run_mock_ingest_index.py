"""MOCK provider: upload + ingestion.run_once with deterministic fake embeddings.

Labels: provider=MOCK, not full production worker pool proof.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
API = "/api/v1"
PASSWORD = "deep-test-safe-password-42"


def _load_env() -> None:
    env_file = ROOT / ".local" / "deep-test" / "env" / "deep-test.env"
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.strip() and "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()
    os.environ["RUN_DB_TESTS"] = "1"


def main() -> int:
    from db_reset_guard import assert_safe_database

    _load_env()
    assert_safe_database()

    from study_platform.core.config import Settings, get_settings
    from study_platform.main import create_app
    from study_platform.persistence.database import get_db, session_factory
    from study_platform.persistence.models.materials import IndexVersion, MaterialChunk
    from study_platform.services import ingestion

    storage = Path(os.environ["STORAGE_DIR"])
    settings = Settings(
        _env_file=None,
        auth_jwt_secret=os.environ["AUTH_JWT_SECRET"],
        storage_dir=storage,
        deepseek_api_key="",
        embedding_api_key="",
    )
    get_settings.cache_clear()
    app = create_app(settings)

    def override_db():
        db = session_factory()()
        try:
            yield db
        finally:
            db.commit()
            db.close()

    app.dependency_overrides[get_db] = override_db
    results: dict = {
        "provider": "MOCK",
        "embedding": "deterministic [0.1]*1024 via patch",
        "users": [],
        "review_status": "synthetic_not_human_curated",
    }

    def fake_embed(texts, **_):
        return [[0.1] * 1024 for _ in texts]

    with patch.object(ingestion, "embed_texts", fake_embed), patch(
        "study_platform.services.models.embed_texts", fake_embed
    ):
        with TestClient(app, headers={"Origin": "http://127.0.0.1:5173"}) as client:
            for label, email, marker in (
                ("u1", "deep-mock-u1@example.test", "MOCK-INDEX-CANARY-U1-ALPHA"),
                ("u2", "deep-mock-u2@example.test", "MOCK-INDEX-CANARY-U2-BRAVO"),
            ):
                reg = client.post(
                    f"{API}/auth/register",
                    json={"email": email, "password": PASSWORD, "display_name": label},
                )
                if reg.status_code not in (201, 409):
                    raise RuntimeError(f"register {label}: {reg.status_code} {reg.text}")
                login = client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})
                token = login.json()["access_token"]
                client.headers["Authorization"] = f"Bearer {token}"
                kb = client.post(f"{API}/knowledge-bases", json={"name": f"MockIngest-{label}"}).json()["id"]
                upload = client.post(
                    f"{API}/knowledge-bases/{kb}/materials",
                    files={"file": (f"{label}.txt", marker.encode(), "text/plain")},
                )
                assert upload.status_code == 202, upload.text
                material_id = upload.json()["material_id"]
                deadline = time.time() + 60
                runs = 0
                while time.time() < deadline:
                    if ingestion.run_once():
                        runs += 1
                    mat = client.get(f"{API}/materials/{material_id}").json()
                    if mat.get("state") == "ready" or mat.get("status") == "ready":
                        break
                    time.sleep(0.05)
                else:
                    results["error"] = f"{label}: ingest timeout"
                    print(json.dumps(results, ensure_ascii=False, indent=2))
                    return 1

                with session_factory()() as db:
                    chunks = db.scalars(
                        sa.select(MaterialChunk)
                        .join(IndexVersion)
                        .where(
                            MaterialChunk.text.contains(marker[:20]),
                            IndexVersion.is_active.is_(True),
                        )
                    ).all()
                    chunk_count = len(chunks)
                results["users"].append(
                    {
                        "label": label,
                        "email": email,
                        "knowledge_base_id": kb,
                        "material_id": material_id,
                        "worker_run_once_calls": runs,
                        "active_chunks_with_marker": chunk_count,
                        "ingest_path": "API upload + ingestion.run_once (MOCK embed)",
                    }
                )
                client.headers.pop("Authorization", None)

    results["ok"] = all(u["active_chunks_with_marker"] >= 1 for u in results["users"])
    out_path = ROOT / ".local" / "deep-test" / "seed" / "mock_ingest_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if results["ok"] else 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())