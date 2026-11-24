"""Cross-user API isolation checks using seeded accounts (P0)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
ACCOUNTS_FILE = ROOT / ".local" / "deep-test" / "seed" / "accounts.json"
API = "/api/v1"


def _load_env() -> None:
    env_file = ROOT / ".local" / "deep-test" / "env" / "deep-test.env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip() and "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())
    os.environ.setdefault("RUN_DB_TESTS", "1")


def _login(client: TestClient, email: str, password: str) -> str:
    response = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    if response.status_code != 200:
        raise RuntimeError(response.text)
    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return token


def main() -> int:
    _load_env()
    if not ACCOUNTS_FILE.exists():
        raise SystemExit("Run seed_accounts.py first")

    data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    users = {row["label"]: row for row in data["users"]}
    password = "deep-test-safe-password-42"

    import sqlalchemy as sa
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import sessionmaker

    from study_platform.core.config import Settings, get_settings
    from study_platform.main import create_app
    from study_platform.persistence.database import get_db

    url = make_url(os.environ["TEST_DATABASE_URL"])
    factory = sessionmaker(
        bind=sa.create_engine(url.set(drivername="postgresql+psycopg")),
        expire_on_commit=False,
    )
    storage = Path(os.environ.get("STORAGE_DIR", ROOT / ".local" / "deep-test" / "storage"))
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
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db

    checks: list[dict] = []
    with TestClient(app, headers={"Origin": "http://localhost:5173"}) as client:
        _login(client, users["u1"]["email"], password)
        u2_kb = users["u2"]["knowledge_base_id"]
        u2_mat = users["u2"]["material_id"]
        for name, call in (
            ("kb_get", lambda: client.get(f"{API}/knowledge-bases/{u2_kb}")),
            ("kb_materials", lambda: client.get(f"{API}/knowledge-bases/{u2_kb}/materials")),
            ("material_get", lambda: client.get(f"{API}/materials/{u2_mat}")),
            ("search_u2_marker", lambda: client.get(f"{API}/search", params={"q": "NIGHTJAR-3-u2-ONLY"})),
        ):
            response = call()
            if name == "search_u2_marker":
                body = response.json() if response.status_code == 200 else {}
                leaked = bool(body.get("items"))
                passed = response.status_code in (403, 404) or (response.status_code == 200 and not leaked)
            else:
                passed = response.status_code in (403, 404)
            checks.append(
                {
                    "as_user": "u1",
                    "check": name,
                    "status_code": response.status_code,
                    "item_count": len(response.json().get("items", [])) if response.status_code == 200 else None,
                    "pass": passed,
                }
            )

    ok = all(row["pass"] for row in checks)
    out = {"ok": ok, "checks": checks, "review_status": "synthetic_not_human_curated"}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())