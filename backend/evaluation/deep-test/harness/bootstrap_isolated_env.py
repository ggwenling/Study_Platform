"""Bootstrap PostgreSQL for deep-test without overwriting repository root .env.

Writes only to .local/deep-test/env/deep-test.env (gitignored).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from shutil import which as shutil_which

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "backend"
ENV_DIR = ROOT / ".local" / "deep-test" / "env"
ENV_FILE = ENV_DIR / "deep-test.env"
STORAGE = ROOT / ".local" / "deep-test" / "storage"


def _backup_root_env() -> Path | None:
    src = ROOT / ".env"
    if not src.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = ENV_DIR / f"root-env-backup-{stamp}.env"
    ENV_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, backup)
    return backup


def main() -> int:
    ENV_DIR.mkdir(parents=True, exist_ok=True)
    STORAGE.mkdir(parents=True, exist_ok=True)
    backup = _backup_root_env()
    if backup:
        print(f"Backed up existing root .env to {backup}")

    env = os.environ.copy()
    env.setdefault("PGHOST", "localhost")
    env.setdefault("PGPORT", "5432")
    env.setdefault("POSTGRES_USER", "postgres")
    env.setdefault("POSTGRES_PASSWORD", "postgres")
    env.setdefault("CI_STUDY_APP_PASSWORD", "deep-test-study-app-secret")
    env.setdefault("CI_STUDY_GRAPH_PASSWORD", "deep-test-study-graph-secret")
    env.setdefault(
        "AUTH_JWT_SECRET", "deep-test-jwt-secret-at-least-32-characters-long"
    )

    uv = shutil_which("uv") or "uv"
    subprocess.check_call(
        [uv, "run", "python", "tests/ci_bootstrap_postgres.py"],
        cwd=BACKEND,
        env=env,
    )
    generated = ROOT / ".env"
    if not generated.exists():
        print("ERROR: ci_bootstrap did not produce root .env", file=sys.stderr)
        return 1
    content = generated.read_text(encoding="utf-8")
    extra = (
        f"STORAGE_DIR={STORAGE}\n"
        "DEEP_TEST_ISOLATION=1\n"
        "LEARNING_STUB_GRADER=true\n"
        "RAG_QUERY_REWRITE_ENABLED=false\n"
    )
    ENV_FILE.write_text(content + extra, encoding="utf-8")
    print(f"Isolated env written: {ENV_FILE}")
    print("Restore your original .env from backup if ci_bootstrap overwrote it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())