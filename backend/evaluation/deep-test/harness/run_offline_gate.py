"""Deep-test gate: isolation proof, offline checks, RUN_DB_TESTS=1 pytest (no model spend).

Records manifest under artifacts/deep-test/<run-id>/ (gitignored).
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from shutil import which
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
DEEP = BACKEND / "evaluation" / "deep-test"
HARNESS = DEEP / "harness"
ARTIFACTS = ROOT / "artifacts" / "deep-test"
BASELINE_SHA = os.environ.get("KNOWLOOP_BASELINE_SHA", "20a20b96ab99bb4176546df6e8972dbd64df51b4")


def git_sha() -> dict[str, str]:
    def rev(arg: list[str]) -> str:
        return subprocess.check_output(["git", *arg], cwd=ROOT, text=True).strip()

    return {
        "baseline_sha": BASELINE_SHA,
        "harness_branch": rev(["rev-parse", "--abbrev-ref", "HEAD"]),
        "harness_sha": rev(["rev-parse", "HEAD"]),
        "harness_subject": rev(["log", "-1", "--format=%s"]),
    }


def versions() -> dict[str, str]:
    def run(cmd: list[str]) -> str:
        try:
            return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            return f"unavailable: {exc}"

    pg_server = "unavailable"
    if which("psql"):
        try:
            pg_server = subprocess.check_output(
                [
                    "psql",
                    "-h",
                    os.environ.get("PGHOST", "localhost"),
                    "-p",
                    os.environ.get("PGPORT", "5432"),
                    "-U",
                    os.environ.get("POSTGRES_USER", "postgres"),
                    "-d",
                    "postgres",
                    "-tAc",
                    "SHOW server_version",
                ],
                env={**os.environ, "PGPASSWORD": os.environ.get("POSTGRES_PASSWORD", "postgres")},
                text=True,
            ).strip()
        except subprocess.CalledProcessError as exc:
            pg_server = f"unavailable: {exc}"

    return {
        "python": run([sys.executable, "--version"]),
        "node": run(["node", "--version"]),
        "postgresql_client": run(["psql", "--version"]),
        "postgresql_server": pg_server,
        "platform": platform.platform(),
        "target": "Python 3.12, Node 22, PostgreSQL 16+pgvector",
    }


def load_isolated_env() -> Path | None:
    path = ROOT / ".local" / "deep-test" / "env" / "deep-test.env"
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip()
    os.environ["RUN_DB_TESTS"] = "1"
    return path


def run_cmd(name: str, cmd: list[str], cwd: Path) -> dict:
    proc = subprocess.run(cmd, cwd=cwd, env=os.environ, text=True, capture_output=True)
    step = {
        "name": name,
        "command": " ".join(cmd),
        "exit_code": proc.returncode,
        "stdout_tail": proc.stdout.splitlines()[-30:],
        "stderr_tail": proc.stderr.splitlines()[-30:],
        "passed": proc.returncode == 0,
    }
    if name == "pytest_db":
        m = re.search(r"(\d+) passed.*?(\d+) skipped", proc.stdout)
        if m:
            step["pytest_passed"] = int(m.group(1))
            step["pytest_skipped"] = int(m.group(2))
        m2 = re.search(r"(\d+) failed", proc.stdout)
        step["pytest_failed"] = int(m2.group(1)) if m2 else 0
    return step


def save_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    run_id = os.environ.get("DEEP_TEST_RUN_ID") or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    out = ARTIFACTS / run_id
    out.mkdir(parents=True, exist_ok=True)

    env_path = load_isolated_env()
    uv = which("uv")
    if not uv:
        raise SystemExit("uv required in PATH")

    manifest: dict = {
        "run_id": run_id,
        "mode": "isolated_db_no_model_spend",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "git": git_sha(),
        "versions": versions(),
        "isolation": {"proven": False, "env_file": str(env_path) if env_path else None},
        "steps": [],
        "model_spend": {"live_calls": 0, "status": "blocked_by_policy"},
    }

    manifest["steps"].append(
        run_cmd("validate_corpus", [sys.executable, str(DEEP / "validate_dataset.py")], ROOT)
    )

    manifest["steps"].append(
        run_cmd(
            "study_eval_rag_dry",
            [
                uv,
                "run",
                "study-eval-rag",
                "--dataset",
                "evaluation/deep-test/questions.jsonl",
            ],
            BACKEND,
        )
    )

    if env_path:
        manifest["steps"].append(
            run_cmd(
                "recreate_test_database",
                [uv, "run", "python", "tests/ci_bootstrap_postgres.py"],
                BACKEND,
            )
        )
        load_isolated_env()

    manifest["steps"].extend(
        [
            run_cmd("ruff", [uv, "run", "ruff", "check", "evaluation/deep-test"], BACKEND),
            run_cmd(
                "pytest_db",
                [uv, "run", "pytest", "-q", "-rs", "--tb=no"],
                BACKEND,
            ),
            run_cmd(
                "migration_roundtrip",
                [uv, "run", "pytest", "-q", "tests/integration/test_migration_roundtrip.py"],
                BACKEND,
            ),
            run_cmd("npm_test", ["npm", "test"], FRONTEND),
            run_cmd("npm_build", ["npm", "run", "build"], FRONTEND),
        ]
    )

    if env_path:
        for name, script in (
            ("prove_isolation", "prove_isolation.py"),
            ("seed_accounts", "seed_accounts.py"),
            ("cross_user_isolation", "run_cross_user_isolation.py"),
        ):
            manifest["steps"].append(
                run_cmd(
                    name,
                    [uv, "run", "python", str(HARNESS / script)],
                    BACKEND,
                )
            )
        proof = next(s for s in manifest["steps"] if s["name"] == "prove_isolation")
        manifest["isolation"]["proven"] = proof.get("passed", False)

    for step in manifest["steps"]:
        if step["name"] == "prove_isolation" and step["passed"]:
            try:
                proof_text = "\n".join(step["stdout_tail"])
                save_json(out / "isolation_proof.json", json.loads(proof_text))
            except json.JSONDecodeError:
                pass
        if step["name"] == "cross_user_isolation":
            try:
                save_json(
                    out / "cross_user_isolation.json",
                    json.loads("\n".join(step["stdout_tail"])),
                )
            except json.JSONDecodeError:
                pass

    if env_path:
        seed = ROOT / ".local" / "deep-test" / "seed" / "accounts.json"
        if seed.exists():
            save_json(out / "seed_accounts_sanitized.json", json.loads(seed.read_text(encoding="utf-8")))

    hard_fail = [s["name"] for s in manifest["steps"] if not s.get("passed")]
    manifest["summary"] = {
        "passed": not hard_fail,
        "failed_steps": hard_fail,
        "pytest_db": next((s for s in manifest["steps"] if s["name"] == "pytest_db"), {}),
        "coverage_note": "Skipped tests are not coverage; see pytest -rs tail in manifest",
    }
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    save_json(out / "manifest.json", manifest)
    (out / "summary.log").write_text(json.dumps(manifest["summary"], ensure_ascii=False) + "\n")

    print(json.dumps(manifest["summary"], ensure_ascii=False))
    print(f"Artifacts: {out}")
    return 0 if not hard_fail else 1


if __name__ == "__main__":
    raise SystemExit(main())