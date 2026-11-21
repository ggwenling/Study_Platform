"""Phase 2 bounded offline suite → artifacts/deep-test/phase2-<run-id>/."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from shutil import which
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "backend"
HARNESS = BACKEND / "evaluation" / "deep-test" / "harness"
ART = ROOT / "artifacts" / "deep-test"
BASELINE = "20a20b96ab99bb4176546df6e8972dbd64df51b4"


def run(name: str, cmd: list[str], cwd: Path) -> dict:
    p = subprocess.run(cmd, cwd=cwd, env=os.environ, text=True, capture_output=True)
    return {
        "name": name,
        "command": " ".join(cmd),
        "exit_code": p.returncode,
        "stdout": p.stdout[-8000:],
        "stderr": p.stderr[-2000:],
        "passed": p.returncode == 0,
    }


def main() -> int:
    run_id = os.environ.get("PHASE2_RUN_ID") or (
        "phase2-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    out = ART / run_id
    out.mkdir(parents=True, exist_ok=True)

    env_file = ROOT / ".local" / "deep-test" / "env" / "deep-test.env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip() and "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ[k.strip()] = v.strip()
    os.environ["RUN_DB_TESTS"] = "1"

    uv = which("uv") or "uv"
    py_steps = [
        ("db_reset_guard", [uv, "run", "python", str(HARNESS / "db_reset_guard.py")], BACKEND),
        ("verify_eval_dataset", [uv, "run", "python", str(HARNESS / "verify_eval_dataset.py")], BACKEND),
        ("study_eval_rag_dry", [uv, "run", "study-eval-rag", "--dataset", "evaluation/deep-test/questions.jsonl"], BACKEND),
        ("model_budget_guard", [uv, "run", "python", str(HARNESS / "check_model_budget.py")], BACKEND),
        (
            "ssrf_unit",
            [uv, "run", "pytest", "-q", "tests/unit/test_web_clip.py", "--tb=no"],
            BACKEND,
        ),
        (
            "worker_recovery_sample",
            [
                uv,
                "run",
                "pytest",
                "-q",
                "tests/unit/test_worker.py",
                "tests/unit/test_ingestion.py::test_stale_or_cancelled_worker_cannot_publish",
                "--tb=no",
            ],
            BACKEND,
        ),
        ("mock_ingest_index", [uv, "run", "python", str(HARNESS / "run_mock_ingest_index.py")], BACKEND),
        ("indexed_isolation", [uv, "run", "python", str(HARNESS / "run_indexed_isolation.py")], BACKEND),
        ("load_baseline", [uv, "run", "python", str(HARNESS / "run_load_baseline.py")], BACKEND),
        ("backup_snapshot", [uv, "run", "python", str(HARNESS / "run_backup_snapshot.py")], BACKEND),
    ]

    manifest = {
        "phase": 2,
        "run_id": run_id,
        "baseline_sha": BASELINE,
        "harness_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "platform": platform.platform(),
        "steps": [run(n, c, d) for n, c, d in py_steps],
        "browser_e2e": {"status": "BLOCKED", "reason": "No headed browser + full stack soak in this VM turn"},
        "live_rag": {"status": "BLOCKED", "reason": "No model budget / API keys"},
        "windows_soak": {"status": "NOT_RUN", "reason": "Protocol only in test-environment.md"},
        "worker_integration_ingestion_db": {
            "status": "PASS_VIA_PHASE1",
            "reason": "tests/integration/test_ingestion_db.py included in offline gate after DB recreate (406 passed); not re-run here to avoid shared-queue claim races",
        },
    }
    manifest["summary"] = {
        "passed": all(s["passed"] for s in manifest["steps"]),
        "failed": [s["name"] for s in manifest["steps"] if not s["passed"]],
    }
    (out / "phase2_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    print(f"Artifacts: {out}")
    return 0 if manifest["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())