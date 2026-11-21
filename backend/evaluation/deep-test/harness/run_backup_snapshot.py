"""Test-owned storage tarball + DB stats checksum snapshot (no production)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[4]
ART = ROOT / "artifacts" / "deep-test"


def _load_env() -> None:
    env = ROOT / ".local" / "deep-test" / "env" / "deep-test.env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.strip() and "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from db_reset_guard import assert_safe_database

    _load_env()
    db_name = assert_safe_database()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ART / f"backup-{run_id}"
    out.mkdir(parents=True, exist_ok=True)
    storage = Path(os.environ["STORAGE_DIR"])
    archive = out / "storage.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        if storage.exists():
            tar.add(storage, arcname="storage")

    url = make_url(os.environ["DATABASE_URL"]).set(drivername="postgresql")
    dsn = url.render_as_string(hide_password=False)
    stats = {}
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        for table in ("users", "knowledge_bases", "materials", "material_chunks"):
            stats[table] = conn.execute(f"SELECT count(*) FROM study.{table}").fetchone()[0]

    dump_path = out / "schema_data.sql"
    subprocess.run(
        [
            "pg_dump",
            "-h",
            os.environ.get("PGHOST", "localhost"),
            "-p",
            os.environ.get("PGPORT", "5432"),
            "-U",
            url.username or "study_app",
            "-d",
            db_name,
            "--schema=study",
            "--data-only",
            "-f",
            str(dump_path),
        ],
        env={**os.environ, "PGPASSWORD": url.password or ""},
        check=False,
        capture_output=True,
    )

    report = {
        "database": db_name,
        "table_counts": stats,
        "storage_archive_sha256": _sha256(archive) if archive.exists() else None,
        "storage_bytes": archive.stat().st_size if archive.exists() else 0,
        "pg_dump_exit": dump_path.exists(),
        "restore_protocol": "NOT_RUN: bounded snapshot only; full restore manual per test-environment.md",
        "review_status": "synthetic_not_human_curated",
    }
    (out / "backup_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())