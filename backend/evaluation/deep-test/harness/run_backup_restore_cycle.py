"""P0 backup + restore to a separate *_test database and storage path (never overwrites source)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "backend"
HARNESS = Path(__file__).resolve().parent
ART = ROOT / "artifacts" / "deep-test"
API = "/api/v1"
TARGET_DB = "study_platform_restore_test"
PASSWORD = "deep-test-safe-password-42"


def _load_env() -> None:
    env = ROOT / ".local" / "deep-test" / "env" / "deep-test.env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.strip() and "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()


def _admin_dsn() -> str:
    return (
        f"postgresql://{os.environ.get('POSTGRES_USER', 'postgres')}:"
        f"{os.environ.get('POSTGRES_PASSWORD', 'postgres')}@"
        f"{os.environ.get('PGHOST', 'localhost')}:"
        f"{os.environ.get('PGPORT', '5432')}/postgres"
    )


def _assert_restore_target(name: str, source: str) -> None:
    if name == source:
        raise SystemExit("Refusing restore onto source database")
    if not name.endswith("_test"):
        raise SystemExit(f"Restore target {name!r} must end with _test")


def _storage_manifest(storage: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not storage.exists():
        return out
    for path in sorted(storage.rglob("*")):
        if path.is_file():
            rel = path.relative_to(storage).as_posix()
            h = hashlib.sha256()
            with path.open("rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    h.update(block)
            out[rel] = h.hexdigest()
    return out


def _db_fingerprint(dsn: str) -> dict:
    fp: dict = {"table_counts": {}, "owners": []}
    with psycopg.connect(dsn, connect_timeout=15) as conn:
        for table in (
            "users",
            "knowledge_bases",
            "materials",
            "material_chunks",
            "conversations",
            "ai_jobs",
            "index_versions",
        ):
            try:
                fp["table_counts"][table] = conn.execute(
                    f"SELECT count(*) FROM study.{table}"
                ).fetchone()[0]
            except psycopg.Error:
                fp["table_counts"][table] = None
        fp["owners"] = [
            {"email": row[0], "id": str(row[1])}
            for row in conn.execute(
                "SELECT email, id FROM study.users ORDER BY email"
            ).fetchall()
        ]
    return fp


def _create_empty_db(name: str) -> None:
    with psycopg.connect(_admin_dsn(), autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (name,),
        )
        conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
        conn.execute(f'CREATE DATABASE "{name}" TEMPLATE template0 ENCODING \'UTF8\'')


def _api_smoke(database_url: str, storage_dir: Path, emails: list[str]) -> dict:
    os.environ["DATABASE_URL"] = database_url
    os.environ["STORAGE_DIR"] = str(storage_dir)
    from study_platform.core.config import Settings, get_settings
    from study_platform.main import create_app
    from study_platform.persistence.database import get_db, session_factory

    settings = Settings(
        _env_file=None,
        auth_jwt_secret=os.environ["AUTH_JWT_SECRET"],
        storage_dir=storage_dir,
        deepseek_api_key="",
        embedding_api_key="",
    )
    get_settings.cache_clear()
    from study_platform.persistence.database import _factory

    _factory.cache_clear()
    app = create_app(settings)

    def override_db():
        db = session_factory()()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    results = []
    with TestClient(app, headers={"Origin": "http://127.0.0.1:5173"}) as client:
        for email in emails:
            login = client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})
            if login.status_code != 200:
                results.append({"email": email, "login": login.status_code, "pass": False})
                continue
            client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
            search = client.get(f"{API}/search", params={"q": "MOCK-INDEX", "include_content": "true"})
            me = client.get(f"{API}/me")
            results.append(
                {
                    "email": email,
                    "login": 200,
                    "me": me.status_code,
                    "search_status": search.status_code,
                    "search_items": len(search.json().get("items", [])) if search.status_code == 200 else None,
                    "pass": login.status_code == 200 and me.status_code == 200 and search.status_code == 200,
                }
            )
            client.headers.pop("Authorization", None)
    return {"api_smoke": results, "pass": all(r["pass"] for r in results)}


def main() -> int:
    sys.path.insert(0, str(HARNESS))
    from db_reset_guard import assert_safe_database

    _load_env()
    source_db = assert_safe_database()
    _assert_restore_target(TARGET_DB, source_db)

    if os.environ.get("BACKUP_SKIP_RECREATE") != "1":
        subprocess.check_call(
            ["uv", "run", "python", "tests/ci_bootstrap_postgres.py"],
            cwd=BACKEND,
            env=os.environ,
        )
        _load_env()

    run_id = os.environ.get("RESTORE_RUN_ID") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ART / f"restore-cycle-{run_id}"
    out.mkdir(parents=True, exist_ok=True)

    subprocess.check_call(
        ["uv", "run", "python", str(HARNESS / "run_mock_ingest_index.py")],
        cwd=BACKEND,
        env=os.environ,
    )
    source_url = make_url(os.environ["DATABASE_URL"])
    source_dsn = source_url.set(drivername="postgresql").render_as_string(hide_password=False)
    storage = Path(os.environ["STORAGE_DIR"])

    source_fp = _db_fingerprint(source_dsn)
    emails = [o["email"] for o in source_fp["owners"]]
    if len(emails) < 2:
        raise SystemExit("Need at least 2 seeded users in source DB before backup")
    source_files = _storage_manifest(storage)
    (out / "source_fingerprint.json").write_text(
        json.dumps({"database": source_db, "db": source_fp, "storage_files": len(source_files)}, indent=2),
        encoding="utf-8",
    )
    (out / "source_storage_checksums.json").write_text(
        json.dumps(source_files, indent=2) + "\n", encoding="utf-8"
    )

    dump_file = out / "source_db.dump"
    subprocess.check_call(
        [
            "pg_dump",
            "-h",
            os.environ.get("PGHOST", "localhost"),
            "-p",
            os.environ.get("PGPORT", "5432"),
            "-U",
            os.environ.get("POSTGRES_USER", "postgres"),
            "-Fc",
            "-f",
            str(dump_file),
            source_db,
        ],
        env={**os.environ, "PGPASSWORD": os.environ.get("POSTGRES_PASSWORD", "postgres")},
    )

    storage_archive = out / "source_storage.tar.gz"
    with tarfile.open(storage_archive, "w:gz") as tar:
        if storage.exists():
            tar.add(storage, arcname="storage")

    _create_empty_db(TARGET_DB)
    restore = subprocess.run(
        [
            "pg_restore",
            "-h",
            os.environ.get("PGHOST", "localhost"),
            "-p",
            os.environ.get("PGPORT", "5432"),
            "-U",
            os.environ.get("POSTGRES_USER", "postgres"),
            "-d",
            TARGET_DB,
            str(dump_file),
        ],
        env={**os.environ, "PGPASSWORD": os.environ.get("POSTGRES_PASSWORD", "postgres")},
        capture_output=True,
        text=True,
    )
    if restore.returncode not in (0, 1):
        raise SystemExit(f"pg_restore failed: {restore.stderr[-2000:]}")
    with psycopg.connect(_admin_dsn(), autocommit=True) as conn:
        conn.execute(f'GRANT CONNECT ON DATABASE "{TARGET_DB}" TO study_app')
        conn.execute(f'GRANT CONNECT ON DATABASE "{TARGET_DB}" TO study_graph')

    restore_storage = ROOT / ".local" / "deep-test" / f"storage-restore-{run_id}"
    if restore_storage.exists():
        shutil.rmtree(restore_storage)
    restore_storage.mkdir(parents=True)
    with tarfile.open(storage_archive, "r:gz") as tar:
        tar.extractall(restore_storage.parent)
    extracted = restore_storage.parent / "storage"
    if extracted.exists() and extracted != restore_storage:
        if restore_storage.exists():
            shutil.rmtree(restore_storage)
        extracted.rename(restore_storage)

    target_url = source_url.set(database=TARGET_DB)
    target_dsn = target_url.set(drivername="postgresql").render_as_string(hide_password=False)
    target_fp = _db_fingerprint(target_dsn)
    target_files = _storage_manifest(restore_storage)

    parity = {
        "table_counts_match": source_fp["table_counts"] == target_fp["table_counts"],
        "owners_match": source_fp["owners"] == target_fp["owners"],
        "storage_checksums_match": source_files == target_files,
    }
    api = _api_smoke(target_url.render_as_string(hide_password=False), restore_storage, emails)

    report = {
        "source_database": source_db,
        "target_database": TARGET_DB,
        "source_storage": str(storage),
        "target_storage": str(restore_storage),
        "snapshot_preserved": str(out),
        "source_pristine": True,
        "parity": parity,
        "api_on_target": api,
        "pass": all(parity.values()) and api["pass"],
        "limitations": [
            "study_graph schema included in pg_dump; not production",
            "No downtime claim; isolated VM only",
        ],
    }
    (out / "restore_manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())