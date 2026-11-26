"""Bounded HTTP load on real localhost uvicorn (not TestClient)."""

from __future__ import annotations

import json
import os
import socket
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "backend"
ART = ROOT / "artifacts" / "deep-test"
DURATION_S = 12
CLIENTS = (1, 5, 10)


def _load_env() -> None:
    env = ROOT / ".local" / "deep-test" / "env" / "deep-test.env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.strip() and "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _health_once(url: str, timeout: float) -> tuple[bool, float]:
    start = time.perf_counter()
    try:
        with urlopen(Request(url, method="GET"), timeout=timeout) as resp:
            ok = resp.status == 200
    except (URLError, TimeoutError, OSError):
        ok = False
    return ok, (time.perf_counter() - start) * 1000


def main() -> int:
    _load_env()
    port = int(os.environ.get("DEEP_TEST_HTTP_PORT") or _free_port())
    base = f"http://127.0.0.1:{port}"
    health = f"{base}/health/live"
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ART / f"http-load-{run_id}"
    out.mkdir(parents=True, exist_ok=True)

    server = subprocess.Popen(
        [
            "uv",
            "run",
            "uvicorn",
            "study_platform.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=BACKEND,
        env={**os.environ, "DEEP_TEST_ISOLATION": "1"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.time() + 30
        ready = False
        while time.time() < deadline:
            ok, _ = _health_once(health, 2.0)
            if ok:
                ready = True
                break
            time.sleep(0.2)
        if not ready:
            err = server.stderr.read() if server.stderr else ""
            raise SystemExit(f"Server not ready on {port}: {err[:500]}")

        report = {
            "endpoint": health,
            "transport": "urllib http localhost",
            "duration_seconds_per_tier": DURATION_S,
            "port": port,
            "clients": [],
            "note": "VM smoke only; not production SLO or hardware-certified P95",
        }
        for workers in CLIENTS:
            latencies: list[float] = []
            errors = 0
            end = time.monotonic() + DURATION_S

            def worker_loop():
                nonlocal errors
                while time.monotonic() < end:
                    ok, ms = _health_once(health, 5.0)
                    if ok:
                        latencies.append(ms)
                    else:
                        errors += 1

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = [pool.submit(worker_loop) for _ in range(workers)]
                for f in as_completed(futs):
                    f.result()

            def pct(p):
                return latencies[int(len(latencies) * p)] if latencies else None

            report["clients"].append(
                {
                    "concurrency": workers,
                    "samples": len(latencies),
                    "errors": errors,
                    "timeouts_or_failures": errors,
                    "p50_ms": statistics.median(latencies) if latencies else None,
                    "p95_ms": pct(0.95),
                }
            )

        report["pass"] = all(c["errors"] == 0 and c["samples"] > 0 for c in report["clients"])
        (out / "http_load_manifest.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["pass"] else 1
    finally:
        server.send_signal(subprocess.signal.SIGTERM)
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    raise SystemExit(main())