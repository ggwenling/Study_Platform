"""Final bounded offline phase orchestrator for PR19."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from shutil import which
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "backend"
HARNESS = BACKEND / "evaluation" / "deep-test" / "harness"
ART = ROOT / "artifacts" / "deep-test"
BASELINE = "20a20b96ab99bb4176546df6e8972dbd64df51b4"


def run_step(name: str, cmd: list[str], cwd: Path) -> dict:
    p = subprocess.run(cmd, cwd=cwd, env=os.environ, text=True, capture_output=True)
    return {
        "name": name,
        "command": " ".join(cmd),
        "exit_code": p.returncode,
        "passed": p.returncode == 0,
        "stdout_tail": p.stdout[-4000:],
        "stderr_tail": p.stderr[-1500:],
    }


def main() -> int:
    run_id = os.environ.get("FINAL_PHASE_RUN_ID") or (
        "final-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    out = ART / run_id
    out.mkdir(parents=True, exist_ok=True)

    env_file = ROOT / ".local" / "deep-test" / "env" / "deep-test.env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip() and "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ[k.strip()] = v.strip()

    uv = which("uv") or "uv"
    steps = [
        ("db_reset_guard", [uv, "run", "python", str(HARNESS / "db_reset_guard.py")], BACKEND),
        (
            "grep_no_bare_item_count_assertion",
            [
                "grep",
                "-r",
                "--include=run_*.py",
                "--include=search_*.py",
                "-E",
                "pass.*len\\(items\\)\\s*==\\s*0",
                str(HARNESS),
            ],
            ROOT,
        ),
        (
            "backup_restore_cycle",
            [uv, "run", "python", str(HARNESS / "run_backup_restore_cycle.py")],
            BACKEND,
        ),
        (
            "worker_process_probe",
            [uv, "run", "python", str(HARNESS / "run_worker_process_probe.py")],
            BACKEND,
        ),
        (
            "http_load_smoke",
            [uv, "run", "python", str(HARNESS / "run_http_load_smoke.py")],
            BACKEND,
        ),
        (
            "indexed_isolation",
            [uv, "run", "python", str(HARNESS / "run_indexed_isolation.py")],
            BACKEND,
        ),
        ("ruff_harness", [uv, "run", "ruff", "check", "evaluation/deep-test/harness"], BACKEND),
        (
            "unit_worker",
            [uv, "run", "pytest", "-q", "tests/unit/test_worker.py", "--tb=no"],
            BACKEND,
        ),
    ]

    manifest = {
        "phase": "final_offline",
        "run_id": run_id,
        "baseline_sha": BASELINE,
        "harness_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "platform": platform.platform(),
        "steps": [],
        "browser_e2e": {"status": "BLOCKED", "reason": "No cloud browser automation in this agent runtime"},
        "live_models": {"status": "BLOCKED", "reason": "No API keys / DEEP_TEST_ALLOW_LIVE"},
        "windows_soak": {"status": "NOT_RUN", "reason": "Requires Windows host per test-environment.md"},
    }

    for name, cmd, cwd in steps:
        if name == "grep_no_bare_item_count_assertion":
            p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
            step = {
                "name": name,
                "command": " ".join(cmd),
                "exit_code": p.returncode,
                "passed": p.returncode == 1,
                "stdout_tail": p.stdout,
                "stderr_tail": p.stderr,
                "note": "exit 1 means pattern absent (good)",
            }
        else:
            step = run_step(name, cmd, cwd)
        manifest["steps"].append(step)

    manifest["summary"] = {
        "passed": all(s["passed"] for s in manifest["steps"]),
        "failed": [s["name"] for s in manifest["steps"] if not s["passed"]],
    }
    (out / "final_phase_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    print(f"Artifacts: {out}")
    return 0 if manifest["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())