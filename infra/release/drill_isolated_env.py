#!/usr/bin/env python3
"""Drill the isolated release-gate environment and a backup restore.

The script refuses any server that is not PostgreSQL on 127.0.0.1:55432, and
it only creates or resets study_release_gate_test, study_release_restore_test,
and study_release_migrate_test. Passwords are written to the gitignored env
file and are removed from command output. This does not call a model API.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
ENV_PATH = ROOT / ".local" / "release-gate" / "env" / "release-gate.env"
STORAGE = ROOT / ".local" / "release-gate" / "storage"
BACKUP_ROOT = ROOT / ".local" / "release-gate" / "backups"
RUN_DIR = ROOT / ".local" / "release-gate" / "run"
EVIDENCE_PATH = ROOT / "infra" / "release" / "drill-evidence.json"
UV = Path.home() / ".local" / "bin" / "uv"
WORKER = BACKEND / ".venv" / "bin" / "study-worker"
HOST = "127.0.0.1"
PORT = 55432
API_PORT = 18000
GATE_DB = "study_release_gate_test"
RESTORE_DB = "study_release_restore_test"
MIGRATE_DB = "study_release_migrate_test"
ALLOWED_DBS = {GATE_DB, RESTORE_DB, MIGRATE_DB}
SYSTEM_DBS = {"postgres", "template0", "template1"}
SYNTHETIC_EMAIL = "release-gate-user@example.test"
SYNTHETIC_NAME = "Release Gate"
STORAGE_NAME = "synthetic-release-gate.txt"
STORAGE_BODY = b"knowloop-release-gate-synthetic-v1\n"
HEAD = "0016_rerank_and_kb_retrieval"
secrets_to_redact: list[str] = []


def redact(text: str) -> str:
    cleaned = text
    for secret in secrets_to_redact:
        if secret:
            cleaned = cleaned.replace(secret, "[redacted]")
    cleaned = re.sub(r"://[^:\s/]+:[^@\s/]+@", "://[redacted]@", cleaned)
    return cleaned


def fail(message: str) -> None:
    raise SystemExit(redact(message))


def remember(secret: str) -> None:
    if secret and secret not in secrets_to_redact:
        secrets_to_redact.append(secret)


def run(cmd: list[str], *, cwd: Path, env: dict[str, str], check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True)
    if check and completed.returncode != 0:
        detail = redact(completed.stdout + "\n" + completed.stderr)[-4000:]
        fail(f"command failed ({completed.returncode}): {cmd[0]} {cmd[-1]}\n{detail}")
    return completed


def listeners(port: int) -> list[str]:
    found: list[str] = []
    port_hex = f"{port:04X}"
    for path in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        if not path.exists():
            continue
        for line in path.read_text().splitlines()[1:]:
            fields = line.split()
            local, state = fields[1], fields[3]
            if state != "0A":
                continue
            address, hex_port = local.split(":")
            if hex_port.upper() != port_hex:
                continue
            if len(address) == 8:
                raw = bytes.fromhex(address)
                ip = socket.inet_ntoa(raw[::-1])
            else:
                ip = "ipv6"
            found.append(ip)
    return found


def assert_loopback_listener() -> None:
    bound = listeners(PORT)
    if not bound:
        fail(f"PostgreSQL is not listening on {PORT}")
    if any(ip not in {HOST, "ipv6"} for ip in bound):
        fail(f"PostgreSQL port {PORT} is not loopback-only: {bound}")
    occupied = listeners(5432)
    if occupied:
        fail(f"port 5432 is occupied ({occupied}); release drill will not share it")


def peer_sql(script: str) -> None:
    completed = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-p", str(PORT), "-v", "ON_ERROR_STOP=1",
         "-d", "postgres", "-f", "-"],
        input=script, text=True, capture_output=True,
    )
    if completed.returncode != 0:
        fail("peer SQL failed\n" + redact(completed.stderr)[-2000:])


def admin_dsn(database: str, password: str) -> str:
    return make_url(
        f"postgresql://postgres@127.0.0.1:{PORT}/{database}"
    ).set(password=password).render_as_string(hide_password=False)


def connect(database: str, password: str) -> psycopg.Connection:
    connection = psycopg.connect(admin_dsn(database, password), autocommit=True, connect_timeout=5)
    port = connection.execute("SHOW port").fetchone()[0]
    listen = connection.execute("SHOW listen_addresses").fetchone()[0]
    data_directory = connection.execute("SHOW data_directory").fetchone()[0]
    current = connection.execute("SELECT current_database()").fetchone()[0]
    if port != str(PORT) or listen != HOST:
        connection.close()
        fail(f"refusing server port={port} listen={listen}")
    if "releasegate" not in data_directory:
        connection.close()
        fail(f"refusing data directory {data_directory}")
    if current != database:
        connection.close()
        fail(f"connected to {current}, expected {database}")
    return connection


def peer_value(statement: str) -> str:
    completed = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-p", str(PORT), "-d", "postgres",
         "-At", "-v", "ON_ERROR_STOP=1", "-c", statement],
        text=True, capture_output=True,
    )
    if completed.returncode != 0:
        fail("peer query failed\n" + redact(completed.stderr)[-2000:])
    return completed.stdout.strip()


def assert_cluster_is_isolated() -> None:
    if peer_value("SHOW port") != str(PORT) or peer_value("SHOW listen_addresses") != HOST:
        fail("peer session is not the loopback release-gate cluster")
    if "releasegate" not in peer_value("SHOW data_directory"):
        fail("peer session is not the releasegate data directory")
    names = set(peer_value("SELECT datname FROM pg_database").splitlines())
    unexpected = names - SYSTEM_DBS - ALLOWED_DBS
    if unexpected:
        fail(f"refusing cluster with unexpected databases: {sorted(unexpected)}")


def quote_password(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", value):
        fail("generated password had unexpected characters")
    return "'" + value + "'"


def ensure_roles_and_databases(admin_password: str, app_password: str, graph_password: str) -> None:
    assert_cluster_is_isolated()
    admin = quote_password(admin_password)
    app = quote_password(app_password)
    graph = quote_password(graph_password)
    peer_sql(f"""
ALTER ROLE postgres WITH PASSWORD {admin};
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'study_release_app') THEN
        CREATE ROLE study_release_app LOGIN PASSWORD {app}
            NOSUPERUSER NOCREATEDB NOCREATEROLE;
    ELSE
        ALTER ROLE study_release_app WITH PASSWORD {app}
            NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'study_release_graph') THEN
        CREATE ROLE study_release_graph LOGIN PASSWORD {graph}
            NOSUPERUSER NOCREATEDB NOCREATEROLE;
    ELSE
        ALTER ROLE study_release_graph WITH PASSWORD {graph}
            NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END $$;
""")
    with connect("postgres", admin_password) as connection:
        for name in (GATE_DB, RESTORE_DB, MIGRATE_DB):
            exists = connection.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
            ).fetchone()
            if exists:
                if name == GATE_DB:
                    continue
                connection.execute(
                    sql.SQL(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = {} AND pid <> pg_backend_pid()"
                    ).format(sql.Literal(name))
                )
                connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
            connection.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(
                    sql.Identifier(name)
                )
            )
            connection.execute(
                sql.SQL("ALTER DATABASE {} SET timezone TO 'UTC'").format(sql.Identifier(name))
            )


def load_or_create_env() -> dict[str, str]:
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    if ENV_PATH.exists():
        values = {}
        for line in ENV_PATH.read_text().splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key] = value
        for key in ("POSTGRES_PASSWORD", "RELEASE_APP_PASSWORD", "RELEASE_GRAPH_PASSWORD",
                    "AUTH_JWT_SECRET", "RELEASE_SYNTHETIC_PASSWORD"):
            remember(values.get(key, ""))
        return values
    import secrets
    values = {
        "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        "RELEASE_APP_PASSWORD": secrets.token_urlsafe(32),
        "RELEASE_GRAPH_PASSWORD": secrets.token_urlsafe(32),
        "AUTH_JWT_SECRET": secrets.token_urlsafe(48),
        "RELEASE_SYNTHETIC_PASSWORD": secrets.token_urlsafe(24),
    }
    for value in values.values():
        remember(value)
    storage = str(STORAGE)
    lines = [
        f"PGHOST={HOST}",
        f"PGPORT={PORT}",
        "POSTGRES_USER=postgres",
        f"POSTGRES_PASSWORD={values['POSTGRES_PASSWORD']}",
        f"RELEASE_APP_PASSWORD={values['RELEASE_APP_PASSWORD']}",
        f"RELEASE_GRAPH_PASSWORD={values['RELEASE_GRAPH_PASSWORD']}",
        f"RELEASE_SYNTHETIC_PASSWORD={values['RELEASE_SYNTHETIC_PASSWORD']}",
        f"RELEASE_DATABASE={GATE_DB}",
        f"RELEASE_RESTORE_DATABASE={RESTORE_DB}",
        f"RELEASE_MIGRATE_DATABASE={MIGRATE_DB}",
        f"DATABASE_URL=postgresql+psycopg://study_release_app:{values['RELEASE_APP_PASSWORD']}@{HOST}:{PORT}/{GATE_DB}",
        f"GRAPH_DATABASE_URL=postgresql://study_release_graph:{values['RELEASE_GRAPH_PASSWORD']}@{HOST}:{PORT}/{GATE_DB}",
        f"MIGRATION_DATABASE_URL=postgresql+psycopg://postgres:{values['POSTGRES_PASSWORD']}@{HOST}:{PORT}/{GATE_DB}",
        f"TEST_DATABASE_URL=postgresql+psycopg://postgres:{values['POSTGRES_PASSWORD']}@{HOST}:{PORT}/{GATE_DB}",
        f"TEST_GRAPH_DATABASE_URL=postgresql://study_release_graph:{values['RELEASE_GRAPH_PASSWORD']}@{HOST}:{PORT}/{GATE_DB}",
        f"STORAGE_DIR={storage}",
        f"AUTH_JWT_SECRET={values['AUTH_JWT_SECRET']}",
        "AUTH_PUBLIC_URL=http://127.0.0.1:15173",
        'ALLOWED_ORIGINS=["http://127.0.0.1:15173","http://127.0.0.1:18000"]',
        "DEEPSEEK_API_KEY=",
        "EMBEDDING_API_KEY=",
        "MODEL_CONNECTIONS_KEY=",
        "RAG_QUERY_REWRITE_ENABLED=false",
        "RETRIEVAL_TOP_K=10",
        "RETRIEVAL_CANDIDATE_K=20",
        "RETRIEVAL_LEXICAL_WEIGHT=0",
        "LEARNING_STUB_GRADER=true",
        "COOKIE_SECURE=false",
        "RUN_DB_TESTS=1",
        "",
    ]
    fd = os.open(ENV_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w") as handle:
        handle.write("\n".join(lines))
    return values


def process_env(values: dict[str, str], database: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = str(UV.parent) + os.pathsep + env.get("PATH", "")
    env.update(values)
    app = values["RELEASE_APP_PASSWORD"]
    graph = values["RELEASE_GRAPH_PASSWORD"]
    admin = values["POSTGRES_PASSWORD"]
    env["DATABASE_URL"] = f"postgresql+psycopg://study_release_app:{app}@{HOST}:{PORT}/{database}"
    env["GRAPH_DATABASE_URL"] = f"postgresql://study_release_graph:{graph}@{HOST}:{PORT}/{database}"
    env["MIGRATION_DATABASE_URL"] = f"postgresql+psycopg://postgres:{admin}@{HOST}:{PORT}/{database}"
    env["TEST_DATABASE_URL"] = env["MIGRATION_DATABASE_URL"]
    env["TEST_GRAPH_DATABASE_URL"] = env["GRAPH_DATABASE_URL"]
    env["PGPASSWORD"] = admin
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("PYTHONSTARTUP", None)
    env["DEEPSEEK_API_KEY"] = ""
    env["EMBEDDING_API_KEY"] = ""
    return env


def official_worker_command() -> list[str]:
    if not WORKER.is_file() or not os.access(WORKER, os.X_OK):
        fail("official study-worker console script is missing")
    text = WORKER.read_text(encoding="utf-8", errors="replace")
    if "from study_platform.cli.worker import main" not in text:
        fail("study-worker console script is not the packaged entry")
    if "study_platform.api" in text or "PYTHONSTARTUP" in text:
        fail("refusing a preload wrapper in place of study-worker")
    return [str(WORKER)]


def alembic(values: dict[str, str], database: str, revision: str, action: str) -> None:
    if action == "downgrade" and database != MIGRATE_DB:
        fail(f"refusing downgrade of {database}")
    with connect(database, values["POSTGRES_PASSWORD"]) as connection:
        current = connection.execute("SELECT current_database()").fetchone()[0]
    if current != database:
        fail(f"alembic target mismatch: {current}")
    run([str(UV), "run", "alembic", action, revision], cwd=BACKEND, env=process_env(values, database))


def grant_runtime(values: dict[str, str], database: str) -> None:
    with connect(database, values["POSTGRES_PASSWORD"]) as connection:
        connection.execute("GRANT USAGE ON SCHEMA study TO study_release_app")
        connection.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA study TO study_release_app"
        )
        connection.execute(
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA study TO study_release_app"
        )
        connection.execute("REVOKE ALL ON SCHEMA study_graph FROM PUBLIC")
        connection.execute("REVOKE ALL ON SCHEMA study_graph FROM study_release_app")
        if connection.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname = 'study_graph'"
        ).fetchone():
            connection.execute("GRANT USAGE, CREATE ON SCHEMA study_graph TO study_release_graph")
            connection.execute(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA study_graph "
                "TO study_release_graph"
            )
            connection.execute(
                "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA study_graph TO study_release_graph"
            )


def prepare_graph_schema(values: dict[str, str], database: str) -> None:
    with connect(database, values["POSTGRES_PASSWORD"]) as connection:
        connection.execute("CREATE SCHEMA IF NOT EXISTS study_graph AUTHORIZATION study_release_graph")
        connection.execute("REVOKE ALL ON SCHEMA study_graph FROM PUBLIC")
        connection.execute("REVOKE ALL ON SCHEMA study_graph FROM study_release_app")


def extension_versions(values: dict[str, str], database: str) -> dict[str, str]:
    with connect(database, values["POSTGRES_PASSWORD"]) as connection:
        rows = connection.execute(
            "SELECT extname, extversion FROM pg_extension WHERE extname IN ('citext', 'vector') ORDER BY 1"
        ).fetchall()
    return {name: version for name, version in rows}


def revision_of(values: dict[str, str], database: str) -> str | None:
    with connect(database, values["POSTGRES_PASSWORD"]) as connection:
        present = connection.execute("SELECT to_regclass('study.alembic_version')").fetchone()[0]
        if present is None:
            return None
        row = connection.execute("SELECT version_num FROM study.alembic_version").fetchone()
    return None if row is None else row[0]


def empty_migration_roundtrip(values: dict[str, str]) -> dict[str, str | None]:
    alembic(values, MIGRATE_DB, "head", "upgrade")
    upgraded = revision_of(values, MIGRATE_DB)
    alembic(values, MIGRATE_DB, "base", "downgrade")
    downgraded = revision_of(values, MIGRATE_DB)
    alembic(values, MIGRATE_DB, "head", "upgrade")
    restored = revision_of(values, MIGRATE_DB)
    with connect(MIGRATE_DB, values["POSTGRES_PASSWORD"]) as connection:
        users = connection.execute("SELECT COUNT(*) FROM study.users").fetchone()[0]
    if users != 0 or upgraded != HEAD or downgraded is not None or restored != HEAD:
        fail(f"empty migration roundtrip failed: {upgraded} -> {downgraded} -> {restored}, users={users}")
    return {"upgraded": upgraded, "downgraded": downgraded, "restored": restored, "users": users}


def wait_http(path: str, *, allow_status: set[int]) -> dict[str, object]:
    last_error = "not attempted"
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://{HOST}:{API_PORT}{path}", timeout=2) as response:
                body = response.read().decode()
                return {"status": response.status, "body": body}
        except urllib.error.HTTPError as error:
            body = error.read().decode()
            if error.code in allow_status:
                return {"status": error.code, "body": body}
            last_error = f"HTTP {error.code} {body[:200]}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            last_error = type(error).__name__
        time.sleep(0.5)
    fail(f"{path} did not become ready: {last_error}")
    return {}


def start_logged(cmd: list[str], env: dict[str, str], log_name: str) -> subprocess.Popen[str]:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    handle = (RUN_DIR / log_name).open("w")
    process = subprocess.Popen(
        cmd, cwd=BACKEND, env=env, stdout=handle, stderr=subprocess.STDOUT, text=True,
        start_new_session=True,
    )
    process.log_handle = handle  # type: ignore[attr-defined]
    return process


def stop_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        if process is not None:
            process.log_handle.close()  # type: ignore[attr-defined]
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    process.log_handle.close()  # type: ignore[attr-defined]


def post_json(path: str, payload: dict, headers: dict[str, str], *, allow: set[int] | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"http://{HOST}:{API_PORT}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:15173", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        body = error.read().decode()
        if allow and error.code in allow:
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = {}
            return error.code, parsed
        fail(f"POST {path} failed HTTP {error.code} {body[:300]}")
    return 0, {}


def app_sessions(values: dict[str, str]) -> int:
    with connect(GATE_DB, values["POSTGRES_PASSWORD"]) as connection:
        return connection.execute(
            """
            SELECT COUNT(*) FROM pg_stat_activity
            WHERE datname = %s AND usename = 'study_release_app'
            """,
            (GATE_DB,),
        ).fetchone()[0]


def seed_graph_marker(values: dict[str, str]) -> str:
    with connect(GATE_DB, values["POSTGRES_PASSWORD"]) as connection:
        columns = connection.execute(
            """
            SELECT column_name, is_nullable, data_type
            FROM information_schema.columns
            WHERE table_schema = 'study_graph' AND table_name = 'checkpoints'
            ORDER BY ordinal_position
            """
        ).fetchall()
        if not columns:
            fail("study_graph.checkpoints was not created")
        existing = connection.execute(
            "SELECT COUNT(*) FROM study_graph.checkpoints WHERE thread_id = %s",
            ("release-gate-synthetic",),
        ).fetchone()[0]
        if existing:
            return "release-gate-synthetic"
        names = []
        values_sql = []
        params: list[object] = []
        for name, nullable, data_type in columns:
            names.append(name)
            if name == "thread_id":
                values_sql.append("%s")
                params.append("release-gate-synthetic")
            elif name == "checkpoint_ns":
                values_sql.append("%s")
                params.append("")
            elif name in {"checkpoint_id", "type"}:
                values_sql.append("%s")
                params.append("release-gate-synthetic")
            elif "json" in data_type:
                values_sql.append("%s::jsonb")
                params.append("{}")
            elif nullable == "YES":
                values_sql.append("NULL")
            else:
                fail(f"cannot fill checkpoints.{name} ({data_type})")
        connection.execute(
            sql.SQL("INSERT INTO study_graph.checkpoints ({}) VALUES ({})").format(
                sql.SQL(", ").join(sql.Identifier(name) for name in names),
                sql.SQL(", ").join(sql.SQL(piece) for piece in values_sql),
            ),
            params,
        )
    return "release-gate-synthetic"


def snapshot(values: dict[str, str], database: str) -> dict[str, object]:
    with connect(database, values["POSTGRES_PASSWORD"]) as connection:
        users = connection.execute(
            "SELECT email::text, display_name FROM study.users ORDER BY email"
        ).fetchall()
        bases = connection.execute(
            "SELECT name FROM study.knowledge_bases ORDER BY name"
        ).fetchall()
        tables = connection.execute(
            """
            SELECT n.nspname || '.' || c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname IN ('study', 'study_graph') AND c.relkind = 'r'
            ORDER BY 1
            """
        ).fetchall()
        marker = connection.execute(
            "SELECT COUNT(*) FROM study_graph.checkpoints WHERE thread_id = %s",
            ("release-gate-synthetic",),
        ).fetchone()[0]
        graph_visible = connection.execute(
            "SET ROLE study_release_graph"
        )
        del graph_visible
        visible = connection.execute(
            "SELECT COUNT(*) FROM study_graph.checkpoints WHERE thread_id = %s",
            ("release-gate-synthetic",),
        ).fetchone()[0]
        connection.execute("RESET ROLE")
    return {
        "revision": revision_of(values, database),
        "extensions": extension_versions(values, database),
        "users": [{"email": email, "display_name": name} for email, name in users],
        "knowledge_bases": [name for (name,) in bases],
        "tables": [name for (name,) in tables],
        "graph_marker_rows": marker,
        "graph_role_can_read_marker": visible,
    }


def backup_and_restore(values: dict[str, str]) -> dict[str, object]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = BACKUP_ROOT / stamp
    dest.mkdir(parents=True)
    env = process_env(values, GATE_DB)
    common = ["--host", HOST, "--port", str(PORT), "--username", "postgres", "--format=custom",
              "--no-owner", "--no-privileges"]
    run(["pg_dump", *common, "--schema=study", f"--file={dest / 'study.dump'}", "--dbname", GATE_DB],
        cwd=ROOT, env=env)
    run(["pg_dump", *common, "--schema=study_graph", f"--file={dest / 'study_graph.dump'}", "--dbname", GATE_DB],
        cwd=ROOT, env=env)
    with tarfile.open(dest / "storage.tar", "w") as archive:
        archive.add(STORAGE / STORAGE_NAME, arcname=STORAGE_NAME)
    checksums = {}
    for name in ("study.dump", "study_graph.dump", "storage.tar"):
        checksums[name] = hashlib.sha256((dest / name).read_bytes()).hexdigest()
    (dest / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in checksums.items()))
    with connect(RESTORE_DB, values["POSTGRES_PASSWORD"]) as connection:
        connection.execute("CREATE EXTENSION IF NOT EXISTS citext")
        connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
    run(["pg_restore", "--host", HOST, "--port", str(PORT), "--username", "postgres",
         "--dbname", RESTORE_DB, "--exit-on-error", str(dest / "study.dump")], cwd=ROOT, env=env)
    run(["pg_restore", "--host", HOST, "--port", str(PORT), "--username", "postgres",
         "--dbname", RESTORE_DB, "--exit-on-error", str(dest / "study_graph.dump")], cwd=ROOT, env=env)
    grant_runtime(values, RESTORE_DB)
    restored_storage = ROOT / ".local" / "release-gate" / "storage-restored"
    if restored_storage.exists():
        shutil.rmtree(restored_storage)
    restored_storage.mkdir(parents=True)
    with tarfile.open(dest / "storage.tar") as archive:
        archive.extractall(restored_storage, filter="data")
    restored_hash = hashlib.sha256((restored_storage / STORAGE_NAME).read_bytes()).hexdigest()
    return {
        "backup_dir": str(dest.relative_to(ROOT)),
        "sha256": checksums,
        "restored_storage_sha256": restored_hash,
        "storage_matches": restored_hash == hashlib.sha256(STORAGE_BODY).hexdigest(),
    }


def product_tree_sha() -> str:
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(
        ["git", "diff", "--name-only", "HEAD", "--", "backend", "frontend"],
        cwd=ROOT, text=True,
    ).strip()
    if dirty:
        fail("refusing to drill while backend or frontend differs from HEAD")
    if sha in {
        "3bd2585e433105e573556add717fab22586e3f62",
        "ac8d8bf6079834c4a9d6c28e407f38a546a659fb",
    }:
        fail("refusing to record an earlier drill as this candidate result")
    return sha


def main() -> None:
    if (ROOT / ".env").exists():
        fail("refusing to run while a repository root .env exists")
    product_sha = product_tree_sha()
    assert_loopback_listener()
    values = load_or_create_env()
    ensure_roles_and_databases(
        values["POSTGRES_PASSWORD"], values["RELEASE_APP_PASSWORD"], values["RELEASE_GRAPH_PASSWORD"]
    )
    roundtrip = empty_migration_roundtrip(values)
    prepare_graph_schema(values, GATE_DB)
    alembic(values, GATE_DB, "head", "upgrade")
    if revision_of(values, GATE_DB) != HEAD:
        fail("gate database did not reach the expected head")
    grant_runtime(values, GATE_DB)
    run([str(UV), "run", "study-setup-checkpointer"], cwd=BACKEND, env=process_env(values, GATE_DB))
    gate_env = process_env(values, GATE_DB)
    gate_env["STORAGE_DIR"] = str(STORAGE)
    STORAGE.mkdir(parents=True, exist_ok=True)
    api = worker = None
    cycles = []
    try:
        api = start_logged(
            [str(UV), "run", "uvicorn", "study_platform.main:create_app", "--factory",
             "--host", HOST, "--port", str(API_PORT)],
            gate_env, "api.log",
        )
        cycles.append({"phase": "first_start", "live": wait_http("/health/live", allow_status={200}),
                       "ready": wait_http("/health/ready", allow_status={200})})
        stop_process(api)
        api = None
        if listeners(API_PORT):
            fail("API port stayed occupied after stop")
        if app_sessions(values) != 0:
            fail("API pool stayed connected after stop")
        worker_cmd = official_worker_command()
        worker = start_logged(worker_cmd, gate_env, "worker.log")
        time.sleep(2)
        worker_sessions = app_sessions(values)
        worker_alive = worker.poll() is None
        stop_process(worker)
        worker = None
        worker_log = (RUN_DIR / "worker.log").read_text(encoding="utf-8", errors="replace")
        if "ImportError" in worker_log:
            fail("official study-worker log contains ImportError")
        if not worker_alive or worker_sessions < 1:
            fail(f"worker did not stay connected (alive={worker_alive}, sessions={worker_sessions})")
        cycles.append({
            "phase": "worker_stopped",
            "entry": "study_platform.cli.worker:main",
            "argv0": "study-worker",
            "preload_wrapper": False,
            "pythonstartup": False,
            "app_sessions_while_running": worker_sessions,
            "import_error": False,
        })
        api = start_logged(
            [str(UV), "run", "uvicorn", "study_platform.main:create_app", "--factory",
             "--host", HOST, "--port", str(API_PORT)],
            gate_env, "api-restart.log",
        )
        cycles.append({"phase": "restart", "live": wait_http("/health/live", allow_status={200}),
                       "ready": wait_http("/health/ready", allow_status={200})})
        status, registered = post_json(
            "/api/v1/auth/register",
            {"email": SYNTHETIC_EMAIL, "password": values["RELEASE_SYNTHETIC_PASSWORD"],
             "display_name": SYNTHETIC_NAME},
            {},
            allow={400},
        )
        if status == 400:
            status, registered = post_json(
                "/api/v1/auth/login",
                {"email": SYNTHETIC_EMAIL, "password": values["RELEASE_SYNTHETIC_PASSWORD"]},
                {},
            )
        token = registered.pop("access_token", None)
        registered.pop("csrf_token", None)
        remember(token or "")
        if status not in {200, 201} or not token or registered.get("user", {}).get("email") != SYNTHETIC_EMAIL:
            fail("synthetic account was not accepted")
        listed = urllib.request.Request(
            f"http://{HOST}:{API_PORT}/api/v1/knowledge-bases",
            headers={"Authorization": f"Bearer {token}", "Origin": "http://127.0.0.1:15173"},
        )
        with urllib.request.urlopen(listed, timeout=10) as response:
            items = json.loads(response.read().decode()).get("items", [])
        existing = next((item for item in items if item.get("name") == "Release Gate Synthetic"), None)
        if existing:
            created = {"name": existing["name"]}
        else:
            kb_status, created = post_json(
                "/api/v1/knowledge-bases",
                {"name": "Release Gate Synthetic", "description": "synthetic isolated drill"},
                {"Authorization": f"Bearer {token}"},
            )
            if kb_status != 201 or created.get("name") != "Release Gate Synthetic":
                fail("synthetic knowledge base was not created")
        token = None
        (STORAGE / STORAGE_NAME).write_bytes(STORAGE_BODY)
        marker = seed_graph_marker(values)
    finally:
        stop_process(worker)
        stop_process(api)
    if listeners(API_PORT):
        fail("API still listening before backup")
    before = snapshot(values, GATE_DB)
    archived = backup_and_restore(values)
    after_gate = snapshot(values, GATE_DB)
    restored = snapshot(values, RESTORE_DB)
    if before != after_gate:
        fail("backup changed the source database")
    if restored["revision"] != before["revision"] or restored["users"] != before["users"]:
        fail("restored database does not match the source rows")
    if restored["knowledge_bases"] != before["knowledge_bases"]:
        fail("restored knowledge base does not match")
    if restored["graph_marker_rows"] != 1 or restored["graph_role_can_read_marker"] != 1:
        fail("restored study_graph marker is not readable")
    if restored["tables"] != before["tables"]:
        fail("restored schema table set differs")
    if not archived["storage_matches"]:
        fail("restored storage hash differs")
    if revision_of(values, GATE_DB) != HEAD:
        fail("source revision changed")
    restore_env = process_env(values, RESTORE_DB)
    restore_env["STORAGE_DIR"] = str(ROOT / ".local" / "release-gate" / "storage-restored")
    api = None
    try:
        api = start_logged(
            [str(UV), "run", "uvicorn", "study_platform.main:create_app", "--factory",
             "--host", HOST, "--port", str(API_PORT)],
            restore_env, "api-restore.log",
        )
        restore_ready = wait_http("/health/ready", allow_status={200})
    finally:
        stop_process(api)
    evidence = {
        "release_ready": False,
        "drill": "pass",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "product_tree_sha": product_sha,
        "product_snapshot": product_sha,
        "prior_drills_not_this_run": [
            "3bd2585e433105e573556add717fab22586e3f62",
            "ac8d8bf6079834c4a9d6c28e407f38a546a659fb",
        ],
        "postgres": {
            "host": HOST,
            "port": PORT,
            "listen": HOST,
            "cluster": "16/releasegate",
            "default_5432": "down",
        },
        "migration_roundtrip_empty_db": roundtrip,
        "health_cycles": cycles,
        "synthetic": {
            "email": SYNTHETIC_EMAIL,
            "knowledge_base": created.get("name"),
            "graph_thread_id": marker,
            "model_calls": 0,
        },
        "source": before,
        "restored": restored,
        "backup": {key: value for key, value in archived.items() if key != "backup_dir"},
        "backup_relative_dir": archived["backup_dir"],
        "restore_health": restore_ready,
        "source_not_downgraded": True,
        "not_verified": [
            "real_50q",
            "d_review",
            "browser_ux",
        ],
    }
    encoded = json.dumps(evidence, indent=2)
    if redact(encoded) != encoded:
        fail("evidence contained a secret and was not written")
    EVIDENCE_PATH.write_text(encoded + "\n")
    print(encoded)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        fail(f"{type(error).__name__}: {error}")
