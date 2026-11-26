"""Emit machine-readable isolation proof for deep-test (no secrets)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    required = ("DATABASE_URL", "TEST_DATABASE_URL", "GRAPH_DATABASE_URL", "STORAGE_DIR")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        print(json.dumps({"ok": False, "missing_env": missing}), file=sys.stderr)
        return 1

    db_url = make_url(os.environ["DATABASE_URL"])
    test_url = make_url(os.environ["TEST_DATABASE_URL"])
    storage = Path(os.environ["STORAGE_DIR"]).resolve()
    proof = {
        "ok": True,
        "database_name": db_url.database,
        "test_database_name": test_url.database,
        "database_is_test_suffix": (db_url.database or "").endswith("_test"),
        "storage_dir": str(storage),
        "storage_under_local_deep_test": str(storage).startswith(str((ROOT / ".local" / "deep-test").resolve())),
        "deep_test_flag": os.environ.get("DEEP_TEST_ISOLATION") == "1",
        "role_checks": {},
    }
    if not proof["database_is_test_suffix"]:
        proof["ok"] = False
        proof["error"] = "DATABASE_URL must target *_test database"

    app_dsn = make_url(os.environ["DATABASE_URL"]).set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    graph_dsn = make_url(os.environ["GRAPH_DATABASE_URL"]).set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    with psycopg.connect(app_dsn, connect_timeout=5) as conn:
        proof["role_checks"]["study_app_reads_users"] = conn.execute(
            "SELECT count(*) FROM study.users"
        ).fetchone()[0]
        try:
            conn.execute("SELECT * FROM study_graph.checkpoints LIMIT 0")
            proof["role_checks"]["study_app_graph_leak"] = True
            proof["ok"] = False
        except psycopg.errors.InsufficientPrivilege:
            proof["role_checks"]["study_app_graph_leak"] = False

    with psycopg.connect(graph_dsn, connect_timeout=5) as conn:
        conn.execute("SELECT * FROM study_graph.checkpoints LIMIT 0")
        try:
            conn.execute("SELECT * FROM study.users LIMIT 0")
            proof["role_checks"]["study_graph_users_leak"] = True
            proof["ok"] = False
        except psycopg.errors.InsufficientPrivilege:
            proof["role_checks"]["study_graph_users_leak"] = False

    print(json.dumps(proof, ensure_ascii=False, indent=2))
    return 0 if proof["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())