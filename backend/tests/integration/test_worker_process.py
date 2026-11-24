"""Official study-worker process on an isolated database.

Spawns the console script. A wrapper that imports the API package first is not
this entry and does not count as a pass.
"""

import hashlib
import json
import os
import re
import subprocess
import sysconfig
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from alembic import command
from study_platform.persistence.models.identity import Material, User
from study_platform.persistence.models.knowledge import KnowledgeBase
from study_platform.persistence.models.materials import MaterialVersion
from study_platform.persistence.models.operations import AiJob

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
WORKER = Path(sysconfig.get_path("scripts")) / ("study-worker.exe" if os.name == "nt" else "study-worker")
_DSN = re.compile(r"postgres(?:ql)?(?:\+\w+)?://\S+")


class _Stub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.calls.append(self.path)
        if self.path.endswith("/embeddings"):
            inputs = body.get("input", [])
            if isinstance(inputs, str):
                inputs = [inputs]
            payload = {
                "object": "list",
                "model": body.get("model"),
                "data": [
                    {"object": "embedding", "index": index, "embedding": [0.1] * 1024}
                    for index in range(len(inputs))
                ],
                "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)},
            }
        elif self.path.endswith("/chat/completions"):
            encoded = json.dumps(body, ensure_ascii=False)
            if "target_count" in encoded:
                content = json.dumps(
                    {"cards": [{"front": "要点", "back": "说明", "source_indices": [1]}]},
                    ensure_ascii=False,
                )
            else:
                content = json.dumps({"topic": "本地", "tags": ["冷启动"]}, ensure_ascii=False)
            payload = {
                "id": "local",
                "object": "chat.completion",
                "created": 1,
                "model": body.get("model"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        else:
            payload = {"error": {"message": "unexpected path"}}
        raw = json.dumps(payload).encode()
        self.send_response(200 if "error" not in payload else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)


def _redact(text):
    return _DSN.sub("postgresql://redacted", text)


def _statuses(factory):
    with factory() as db:
        rows = db.scalars(sa.select(AiJob).order_by(AiJob.created_at, AiJob.id)).all()
        return {
            (row.job_type, str(row.resource_id)): (row.status, row.failure_code, row.progress)
            for row in rows
        }


def _wait(factory, expected, process, log_path, timeout):
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        last = _statuses(factory)
        if all(last.get(key, (None,))[0] == "succeeded" for key in expected):
            return last
        if any(last.get(key, (None,))[0] in {"failed", "cancelled"} for key in expected):
            break
        time.sleep(0.25)
    log = _redact(log_path.read_text(encoding="utf-8", errors="replace")[-4000:])
    raise AssertionError(f"jobs={last} exit={process.poll()} log={log}")


def test_study_worker_runs_ingest_classification_and_flashcards(tmp_path, monkeypatch):
    if os.getenv("RUN_DB_TESTS") != "1" or not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("explicit PostgreSQL test database required")
    if not WORKER.is_file():
        pytest.fail("study-worker console script is missing")
    url = make_url(os.environ["TEST_DATABASE_URL"])
    if not (url.database or "").endswith("_test") or {"dbname", "service", "servicefile"} & set(url.query):
        pytest.fail("isolated *_test database required")
    url = url.set(drivername="postgresql+psycopg")
    name = f"study_worker_{uuid4().hex}_test"
    admin = sa.create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    created = False
    engine = None
    process = None
    server = None
    try:
        with admin.connect() as connection:
            if not connection.scalar(
                sa.text("SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname=current_user")
            ):
                pytest.skip("database role lacks CREATEDB")
            quoted = connection.dialect.identifier_preparer.quote(name)
            connection.exec_driver_sql(f"CREATE DATABASE {quoted}")
            created = True
        target = url.set(database=name)
        monkeypatch.setenv("MIGRATION_DATABASE_URL", target.render_as_string(hide_password=False))
        command.upgrade(Config(str(BACKEND / "alembic.ini")), "head")
        engine = sa.create_engine(target)
        factory = sessionmaker(engine, expire_on_commit=False)
        owner, kb_id, material_id, version_id, ingest_id = (uuid4() for _ in range(5))
        source = "冷启动闪卡资料。向量索引完成后应生成记忆闪卡。"
        raw = source.encode()
        storage = tmp_path / "storage"
        (storage / str(version_id)).mkdir(parents=True)
        (storage / str(version_id) / "source").write_bytes(raw)
        with factory.begin() as db:
            db.add(User(id=owner, email=f"{owner}@example.test", password_hash="unused", display_name="Worker"))
            db.flush()
            db.add(KnowledgeBase(id=kb_id, owner_user_id=owner, name="冷启动库"))
            db.flush()
            db.add(Material(
                id=material_id, owner_user_id=owner, knowledge_base_id=kb_id,
                title="冷启动资料", kind="other", status="active",
            ))
            db.flush()
            db.add(MaterialVersion(
                id=version_id, material_id=material_id, version_no=1, is_current=True,
                source_mime="text/plain", source_sha256=hashlib.sha256(raw).hexdigest(),
                byte_size=len(raw), object_key=str(uuid4()), parse_status="pending",
            ))
            db.flush()
            db.add(AiJob(
                id=ingest_id, owner_user_id=owner, job_type="ingest",
                resource_type="material_version", resource_id=version_id, status="queued",
                progress=0, payload={}, attempt_count=0, max_attempts=3, fencing=0,
                next_run_at=datetime.now(UTC),
            ))

        server = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
        server.calls = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}/v1"
        log_path = tmp_path / "study-worker.log"
        env = os.environ.copy()
        env.update(
            DATABASE_URL=target.render_as_string(hide_password=False),
            STORAGE_DIR=str(storage),
            DEEPSEEK_API_KEY="local-stub-not-a-provider",
            DEEPSEEK_BASE_URL=base,
            EMBEDDING_API_KEY="local-stub-not-a-provider",
            EMBEDDING_BASE_URL=base,
            EMBEDDING_MODEL="synthetic-embed",
            CHAT_MODEL="synthetic-chat",
            LEARNING_STUB_GRADER="false",
            PYTHONUNBUFFERED="1",
        )
        env.pop("PYTHONSTARTUP", None)
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [str(WORKER)],
                cwd=BACKEND,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        ingest_key = ("ingest", str(version_id))
        seen = _wait(factory, [ingest_key], process, log_path, 90)
        with factory() as db:
            classification = db.scalar(sa.select(AiJob).where(AiJob.job_type == "classification"))
        assert classification is not None
        class_key = ("classification", str(classification.resource_id))
        seen = _wait(factory, [ingest_key, class_key], process, log_path, 60)
        flash_id = uuid4()
        with factory.begin() as db:
            db.add(AiJob(
                id=flash_id, owner_user_id=owner, job_type="flashcards",
                resource_type="knowledge_base", resource_id=kb_id, status="queued",
                progress=0,
                payload={
                    "body": {"count": 1, "material_ids": [str(material_id)], "title": "记忆闪卡"},
                    "title": "记忆闪卡",
                    "llm_connection": {"id": None, "kind": "llm"},
                },
                attempt_count=0, max_attempts=3, fencing=0, next_run_at=datetime.now(UTC),
            ))
        flash_key = ("flashcards", str(kb_id))
        seen = _wait(factory, [ingest_key, class_key, flash_key], process, log_path, 60)
        log_text = _redact(log_path.read_text(encoding="utf-8", errors="replace"))
        assert "Worker stopped" not in log_text
        assert "ImportError" not in log_text
        assert process.poll() is None
        assert {key: seen[key][0] for key in (ingest_key, class_key, flash_key)} == {
            ingest_key: "succeeded",
            class_key: "succeeded",
            flash_key: "succeeded",
        }
        assert any(path.endswith("/embeddings") for path in server.calls)
        assert any(path.endswith("/chat/completions") for path in server.calls)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if server is not None:
            server.shutdown()
            server.server_close()
        if engine is not None:
            engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.execute(
                    sa.text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :name AND pid <> pg_backend_pid()"
                    ),
                    {"name": name},
                )
                quoted = connection.dialect.identifier_preparer.quote(name)
                connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {quoted}")
        admin.dispose()
