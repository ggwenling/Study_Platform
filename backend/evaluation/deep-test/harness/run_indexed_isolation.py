"""Cross-user retrieval with indexed chunks (positive control + ownership-based negative)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import sqlalchemy as sa
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
API = "/api/v1"
PASSWORD = "deep-test-safe-password-42"
REPORT = ROOT / ".local" / "deep-test" / "seed" / "mock_ingest_report.json"
HARNESS_DIR = Path(__file__).resolve().parent


def _load_env() -> None:
    env = ROOT / ".local" / "deep-test" / "env" / "deep-test.env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.strip() and "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()


def _login(client: TestClient, email: str) -> None:
    r = client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"


def main() -> int:
    sys.path.insert(0, str(HARNESS_DIR))
    from search_leak_checks import classify_search_items

    if not REPORT.exists():
        raise SystemExit("Run run_mock_ingest_index.py first")
    data = json.loads(REPORT.read_text(encoding="utf-8"))
    users = {u["label"]: u for u in data["users"]}
    _load_env()

    from study_platform.core.config import Settings, get_settings
    from study_platform.main import create_app
    from study_platform.persistence.database import get_db, session_factory

    settings = Settings(
        _env_file=None,
        auth_jwt_secret=os.environ["AUTH_JWT_SECRET"],
        storage_dir=Path(os.environ["STORAGE_DIR"]),
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
            db.close()

    app.dependency_overrides[get_db] = override_db
    checks: list[dict] = []

    with TestClient(app, headers={"Origin": "http://127.0.0.1:5173"}) as client:
        unauth = client.get(f"{API}/search", params={"q": "MOCK-INDEX-CANARY-U2-BRAVO", "include_content": "true"})
        checks.append(
            {
                "check": "unauth_search",
                "status_code": unauth.status_code,
                "pass": unauth.status_code in (401, 403),
            }
        )

        for label, marker_suffix in (("u1", "U1-ALPHA"), ("u2", "U2-BRAVO")):
            _login(client, users[label]["email"])
            q = f"MOCK-INDEX-CANARY-{marker_suffix}"
            search = client.get(f"{API}/search", params={"q": q, "include_content": "true"})
            items = search.json().get("items", []) if search.status_code == 200 else []
            checks.append(
                {
                    "check": f"owner_positive_{label}",
                    "query": q,
                    "status_code": search.status_code,
                    "item_count": len(items),
                    "pass": search.status_code == 200 and len(items) >= 1,
                }
            )
            client.headers.pop("Authorization", None)

        u2_marker = "MOCK-INDEX-CANARY-U2-BRAVO"
        with session_factory()() as db:
            from study_platform.persistence.models.identity import User

            u1_row = db.scalar(sa.select(User).where(User.email == users["u1"]["email"]))
            u2_row = db.scalar(sa.select(User).where(User.email == users["u2"]["email"]))
            assert u1_row and u2_row

        _login(client, users["u1"]["email"])
        search = client.get(f"{API}/search", params={"q": u2_marker, "include_content": "true"})
        items = search.json().get("items", []) if search.status_code == 200 else []
        with session_factory()() as db:
            classified, leaks = classify_search_items(
                db,
                items,
                requester_user_id=str(u1_row.id),
                peer_user_id=str(u2_row.id),
                peer_knowledge_base_id=users["u2"]["knowledge_base_id"],
                peer_material_id=users["u2"]["material_id"],
                peer_exact_canary=u2_marker,
            )
        checks.append(
            {
                "check": "u1_must_not_see_u2_owned_or_exact_canary",
                "query": u2_marker,
                "item_count": len(items),
                "unauthorized_leaks": len(leaks),
                "leak_sample": leaks[:5],
                "pass": len(leaks) == 0,
                "provider": "MOCK-indexed",
                "note": "item_count may be >0 when OR-lexical terms overlap own documents; not a leak by itself",
            }
        )
        client.headers.pop("Authorization", None)

        _login(client, users["u1"]["email"])
        note = client.post(
            f"{API}/knowledge-bases/{users['u1']['knowledge_base_id']}/notes",
            json={"title": "隔离笔记", "content_md": "MOCK-NOTE-U1-SECRET"},
        )
        note_id = note.json()["id"] if note.status_code == 201 else None
        client.headers.pop("Authorization", None)
        _login(client, users["u2"]["email"])
        denied_note = client.get(f"{API}/notes/{note_id}") if note_id else None
        checks.append(
            {
                "check": "notes_cross_user",
                "status_code": denied_note.status_code if denied_note else None,
                "pass": denied_note is not None and denied_note.status_code in (403, 404),
            }
        )

    out = {
        "ok": all(c["pass"] for c in checks),
        "checks": checks,
        "ingest_evidence": str(REPORT),
        "review_status": "synthetic_not_human_curated",
        "not_real_rag_quality": True,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())