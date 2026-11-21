"""Offline deep-test gate: no external model calls.

Records manifest under artifacts/deep-test/<run-id>/ (gitignored).
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
DEEP = BACKEND / "evaluation" / "deep-test"
ARTIFACTS = ROOT / "artifacts" / "deep-test"


def git_sha() -> dict[str, str]:
    def rev(arg: list[str]) -> str:
        return (
            subprocess.check_output(["git", *arg], cwd=ROOT, text=True).strip()
        )

    return {
        "baseline_branch": rev(["rev-parse", "--abbrev-ref", "HEAD"]),
        "baseline_sha": rev(["rev-parse", "HEAD"]),
        "baseline_subject": rev(["log", "-1", "--format=%s"]),
        "harness_paths": [
            "backend/evaluation/deep-test/harness/run_offline_gate.py",
            "backend/evaluation/deep-test/validate_dataset.py",
        ],
    }


def versions() -> dict[str, str]:
    def run(cmd: list[str]) -> str:
        try:
            return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            return f"unavailable: {exc}"

    py = run([sys.executable, "--version"])
    node = run(["node", "--version"])
    pg = run(["psql", "--version"])
    return {
        "python": py,
        "node": node,
        "postgresql_client": pg,
        "platform": platform.platform(),
        "note": "Target stack: Python 3.12, Node 22, PostgreSQL 16+pgvector",
    }


def load_isolated_env() -> Path | None:
    path = ROOT / ".local" / "deep-test" / "env" / "deep-test.env"
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())
    os.environ.setdefault("RUN_DB_TESTS", "1")
    return path


def run_cmd(name: str, cmd: list[str], cwd: Path, env: dict | None = None) -> dict:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=env or os.environ,
        text=True,
        capture_output=True,
    )
    return {
        "name": name,
        "command": " ".join(cmd),
        "exit_code": proc.returncode,
        "stdout_tail": proc.stdout.splitlines()[-20:],
        "stderr_tail": proc.stderr.splitlines()[-20:],
        "passed": proc.returncode == 0,
    }


def main() -> int:
    run_id = os.environ.get("DEEP_TEST_RUN_ID") or datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    ) + "-" + uuid4().hex[:8]
    out = ARTIFACTS / run_id
    out.mkdir(parents=True, exist_ok=True)

    env_path = load_isolated_env()
    manifest: dict = {
        "run_id": run_id,
        "mode": "offline_no_model_spend",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "git": git_sha(),
        "versions": versions(),
        "isolation": {
            "env_file": str(env_path) if env_path else None,
            "storage_dir": os.environ.get("STORAGE_DIR"),
            "database": (
                os.environ.get("DATABASE_URL", "").split("@")[-1]
                if os.environ.get("DATABASE_URL")
                else None
            ),
            "isolated": bool(env_path and "study_platform_test" in os.environ.get("DATABASE_URL", "")),
        },
        "steps": [],
        "model_spend": {
            "live_calls": 0,
            "status": "blocked_by_policy",
            "required_user_actions": [
                "Approve dedicated test model connection in KnowLoop Model Connections",
                "Set explicit spending cap and record in test-environment.md",
                "Export DEEPSEEK_API_KEY / EMBEDDING_API_KEY only into .local/deep-test/env/deep-test.env",
            ],
        },
    }

    uv = shutil_which("uv")
    py_steps = [
        (
            "deep_test_validate_corpus",
            [sys.executable, str(DEEP / "validate_dataset.py")],
            ROOT,
        ),
    ]
    if uv:
        py_steps.extend(
            [
                ("ruff", [uv, "run", "ruff", "check", "."], BACKEND),
                (
                    "pytest",
                    [uv, "run", "pytest", "-q", "--tb=no"],
                    BACKEND,
                ),
            ]
        )
    else:
        manifest["steps"].append(
            {
                "name": "uv_missing",
                "passed": False,
                "note": "Install uv for full pytest/ruff gate",
            }
        )

    for name, cmd, cwd in py_steps:
        manifest["steps"].append(run_cmd(name, cmd, cwd))

    node = shutil_which("node")
    npm = shutil_which("npm")
    if node and npm and (FRONTEND / "package.json").exists():
        for name, script in (("npm_test", ["npm", "test"]), ("npm_build", ["npm", "run", "build"])):
            manifest["steps"].append(run_cmd(name, script, FRONTEND))
    else:
        manifest["steps"].append({"name": "frontend", "passed": False, "note": "node/npm unavailable"})

    # Lightweight local load probe (health only, mocked label if API down)
    manifest["load_baseline"] = {
        "status": "BLOCKED",
        "reason": "API/worker not started in offline prep phase",
        "planned_clients": [1, 5, 10],
        "duration_seconds": 30,
    }

    passed = all(step.get("passed", False) for step in manifest["steps"] if "passed" in step)
    manifest["summary"] = {
        "passed": passed,
        "step_count": len(manifest["steps"]),
        "failed": [s["name"] for s in manifest["steps"] if not s.get("passed")],
    }
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()

    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "summary.log").write_text(
        json.dumps(manifest["summary"], ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    print(f"Artifacts: {out}")
    return 0 if passed else 1


def shutil_which(cmd: str) -> str | None:
    from shutil import which

    return which(cmd)


if __name__ == "__main__":
    raise SystemExit(main())