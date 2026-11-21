"""Bounded concurrent /health/live baseline (no model endpoints)."""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
DURATION_S = 15
CLIENTS = (1, 5, 10)


def _load_env() -> None:
    env = ROOT / ".local" / "deep-test" / "env" / "deep-test.env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.strip() and "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()


def main() -> int:
    _load_env()
    from study_platform.core.config import Settings, get_settings
    from study_platform.main import create_app
    from study_platform.persistence.database import get_db, session_factory

    settings = Settings(
        _env_file=None,
        auth_jwt_secret=os.environ["AUTH_JWT_SECRET"],
        storage_dir=Path(os.environ["STORAGE_DIR"]),
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
    client = TestClient(app)
    report = {
        "endpoint": "/health/live",
        "duration_seconds_per_tier": DURATION_S,
        "clients": [],
        "provider": "MOCK-in-process-TestClient",
        "note": "Not representative of production networking; VM baseline only",
    }

    for workers in CLIENTS:
        latencies: list[float] = []
        errors = 0
        end = time.perf_counter() + DURATION_S
        def one_call():
            t0 = time.perf_counter()
            r = client.get("/health/live")
            return (time.perf_counter() - t0) * 1000, r.status_code

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = []
            while time.perf_counter() < end:
                futures.append(pool.submit(one_call))
                if len(futures) > workers * 200:
                    break
            for fut in as_completed(futures):
                try:
                    ms, code = fut.result()
                    if code != 200:
                        errors += 1
                    else:
                        latencies.append(ms)
                except Exception:
                    errors += 1

        latencies.sort()
        def pct(p):
            return latencies[int(len(latencies) * p)] if latencies else None

        report["clients"].append(
            {
                "concurrency": workers,
                "samples": len(latencies),
                "errors": errors,
                "p50_ms": statistics.median(latencies) if latencies else None,
                "p95_ms": pct(0.95),
            }
        )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())