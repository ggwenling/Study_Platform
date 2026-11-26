"""Real study-worker subprocess probe (distinct from unit tests).

Without live embedding keys, queued ingest may fail/retry; we verify process start/stop
and optional recovery via bounded harness run_once with MOCK patch.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "backend"
HARNESS = Path(__file__).resolve().parent
ART = ROOT / "artifacts" / "deep-test"
API = "/api/v1"
PASSWORD = "deep-test-safe-password-42"


def _load_env() -> None:
    env = ROOT / ".local" / "deep-test" / "env" / "deep-test.env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.strip() and "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()


def main() -> int:
    _load_env()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ART / f"worker-process-{run_id}"
    out.mkdir(parents=True, exist_ok=True)

    report_path = ROOT / ".local" / "deep-test" / "seed" / "mock_ingest_report.json"
    if not report_path.exists():
        subprocess.check_call(
            ["uv", "run", "python", str(HARNESS / "run_mock_ingest_index.py")],
            cwd=BACKEND,
            env=os.environ,
        )

    from study_platform.core.config import Settings, get_settings
    from study_platform.main import create_app
    from study_platform.persistence.database import get_db, session_factory
    from study_platform.persistence.models.operations import AiJob
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
            db.close()

    app.dependency_overrides[get_db] = override_db
    job_id = None
    with TestClient(app, headers={"Origin": "http://127.0.0.1:5173"}) as client:
        email = "deep-mock-u1@example.test"
        login = client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})
        assert login.status_code == 200, login.text
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        kbs = client.get(f"{API}/knowledge-bases").json()
        kb_id = kbs["items"][0]["id"] if kbs.get("items") else None
        assert kb_id
        upload = client.post(
            f"{API}/knowledge-bases/{kb_id}/materials",
            files={"file": ("interrupt-probe.txt", b"WORKER-PROCESS-PROBE-CONTENT", "text/plain")},
        )
        assert upload.status_code == 202, upload.text
        job_id = upload.json().get("job_id")

    worker = subprocess.Popen(
        ["uv", "run", "study-worker"],
        cwd=BACKEND,
        env=os.environ,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(1.5)
    worker.send_signal(signal.SIGTERM)
    try:
        exit_code = worker.wait(timeout=10)
    except subprocess.TimeoutExpired:
        worker.kill()
        exit_code = -9

    with session_factory()() as db:
        job = db.get(AiJob, job_id) if job_id else None
        status_after_interrupt = job.status if job else None

    recovered = False
    def fake_embed(texts, **_):
        return [[0.1] * 1024 for _ in texts]

    with patch.object(ingestion, "embed_texts", fake_embed):
        recovered = bool(ingestion.run_once())
    with session_factory()() as db:
        job = db.get(AiJob, job_id) if job_id else None
        status_after_mock_recovery = job.status if job else None

    report = {
        "kind": "process_subprocess",
        "distinction": "unit tests in tests/unit/test_worker.py do not spawn study-worker",
        "worker_exit_code": exit_code,
        "job_id": str(job_id) if job_id else None,
        "status_after_sigterm": status_after_interrupt,
        "mock_recovery_run_once": recovered,
        "status_after_mock_recovery": status_after_mock_recovery,
        "provider_recovery": "MOCK patch in harness only (not subprocess)",
        "pass": exit_code in (0, -15, 143) and job is not None,
    }
    (out / "worker_process_manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())