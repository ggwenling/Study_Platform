"""Seed two committed test accounts via public HTTP API (in-process TestClient).

Writes sanitized account metadata to .local/deep-test/seed/accounts.json (gitignored).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[4]
SEED_DIR = ROOT / ".local" / "deep-test" / "seed"
ACCOUNTS_FILE = SEED_DIR / "accounts.json"
API = "/api/v1"


def _load_env_file() -> None:
    env_file = ROOT / ".local" / "deep-test" / "env" / "deep-test.env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())
    os.environ.setdefault("RUN_DB_TESTS", "1")


def _require_test_db() -> sessionmaker:
    url = os.getenv("TEST_DATABASE_URL")
    if not url or os.getenv("RUN_DB_TESTS") != "1":
        raise SystemExit("RUN_DB_TESTS=1 and TEST_DATABASE_URL required")
    parsed = make_url(url)
    if not (parsed.database or "").endswith("_test"):
        raise SystemExit("TEST_DATABASE_URL must end with _test")
    engine = sa.create_engine(parsed.set(drivername="postgresql+psycopg"))
    return sessionmaker(bind=engine, expire_on_commit=False)


def _auth(client: TestClient, token: str) -> None:
    client.headers["Authorization"] = f"Bearer {token}"


def main() -> int:
    _load_env_file()
    storage = Path(os.environ.get("STORAGE_DIR", ROOT / ".local" / "deep-test" / "storage"))
    storage.mkdir(parents=True, exist_ok=True)
    SEED_DIR.mkdir(parents=True, exist_ok=True)

    from study_platform.core.config import Settings, get_settings
    from study_platform.main import create_app
    from study_platform.persistence.database import get_db

    settings = Settings(
        _env_file=None,
        auth_jwt_secret=os.environ.get(
            "AUTH_JWT_SECRET", "deep-test-jwt-secret-at-least-32-characters-long"
        ),
        storage_dir=storage,
        deepseek_api_key="",
        embedding_api_key="",
    )
    get_settings.cache_clear()
    factory = _require_test_db()
    app = create_app(settings)

    def override_db():
        db = factory()
        try:
            yield db
        finally:
            db.commit()
            db.close()

    app.dependency_overrides[get_db] = override_db

    password = "deep-test-safe-password-42"
    accounts: dict = {"review_status": "synthetic_not_human_curated", "users": []}

    with TestClient(app, headers={"Origin": "http://localhost:5173"}) as client:
        for email, label in (
            ("deep-u1@example.test", "u1"),
            ("deep-u2@example.test", "u2"),
        ):
            reg = client.post(
                f"{API}/auth/register",
                json={
                    "email": email,
                    "password": password,
                    "display_name": f"Deep {label.upper()}",
                },
            )
            if reg.status_code not in (201, 409, 400):
                raise RuntimeError(f"register {email}: {reg.status_code} {reg.text}")
            login = client.post(f"{API}/auth/login", json={"email": email, "password": password})
            if login.status_code != 200:
                raise RuntimeError(f"login {email}: {login.status_code} {login.text}")
            token = login.json()["access_token"]
            _auth(client, token)
            user_id = login.json()["user"]["id"]
            kb_name = f"DeepTest-{label.upper()}-KB"
            kb_resp = client.post(f"{API}/knowledge-bases", json={"name": kb_name})
            if kb_resp.status_code == 201:
                kb_id = kb_resp.json()["id"]
            else:
                listed = client.get(f"{API}/knowledge-bases").json()["items"]
                kb_id = next(row["id"] for row in listed if row["name"] == kb_name)
            secret_marker = f"NIGHTJAR-3-{label}-ONLY"
            upload = client.post(
                f"{API}/knowledge-bases/{kb_id}/materials",
                files={"file": (f"{label}-canary.txt", secret_marker.encode(), "text/plain")},
            )
            assert upload.status_code == 202, upload.text
            accounts["users"].append(
                {
                    "label": label,
                    "user_id": user_id,
                    "email": email,
                    "knowledge_base_id": kb_id,
                    "knowledge_base_name": kb_name,
                    "material_id": upload.json()["material_id"],
                    "canary_plaintext_marker": secret_marker,
                }
            )
            client.headers.pop("Authorization", None)

    ACCOUNTS_FILE.write_text(json.dumps(accounts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Seeded {len(accounts['users'])} users -> {ACCOUNTS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())