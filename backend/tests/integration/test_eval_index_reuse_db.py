"""Real PostgreSQL checks for retained evaluation indexes.

Vectors are handmade. Generation is exercised through the real client code with
a mocked HTTP transport, not a paid provider. Set RUN_DB_TESTS=1 and point
TEST_DATABASE_URL at a disposable PostgreSQL *_test database whose role can
create databases.
"""

import asyncio
import hashlib
import json
import math
import os
import sys
from contextlib import contextmanager
from uuid import uuid4

import dotenv
import httpx
import httpx2
import pytest
import sqlalchemy as sa
from openai import APIStatusError

from study_platform.cli.evaluate_rag import EvaluationFailed, isolated_database, live_evaluation
from study_platform.core.config import Settings, get_settings
from study_platform.services.chunking import SEMANTIC_PROFILE
from study_platform.services.eval_index_reuse import (
    EMBEDDING_DIM,
    IndexReuseError,
    assert_database_name,
    build_manifest,
    code_schema_revision,
    import_index,
    new_database_name,
    read_only_retrieval,
    read_snapshot,
    retained_database,
    reuse_target_url,
    verify_index_contents,
)
from study_platform.services.eval_index_reuse import (
    test_database_url as parse_test_database_url,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1" or not os.getenv("TEST_DATABASE_URL"),
    reason="set RUN_DB_TESTS=1 and TEST_DATABASE_URL to a disposable *_test database",
)


@pytest.fixture(autouse=True)
def _keep_secret_files_unread(monkeypatch):
    """Do not open an application env file. The test URL comes from the process environment."""
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *_args, **_kwargs: False)
    env_module = sys.modules.get("alembic.env")
    if env_module is not None:
        monkeypatch.setattr(env_module, "load_dotenv", lambda *_args, **_kwargs: False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    raw = os.getenv("TEST_DATABASE_URL")
    if raw:
        monkeypatch.setenv("DATABASE_URL", raw)
        monkeypatch.setenv("MIGRATION_DATABASE_URL", raw)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _url():
    raw = os.environ["TEST_DATABASE_URL"]
    url = parse_test_database_url(raw)
    if not (url.database or "").endswith("_test"):
        raise RuntimeError("TEST_DATABASE_URL must name a *_test database")
    return raw, url


def _admin():
    _raw, url = _url()
    return sa.create_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )


def _exists(name):
    engine = _admin()
    try:
        with engine.connect() as connection:
            return bool(
                connection.scalar(
                    sa.text("SELECT 1 FROM pg_database WHERE datname = :name"),
                    {"name": name},
                )
            )
    finally:
        engine.dispose()


def _drop(name):
    assert_database_name(name)
    engine = _admin()
    try:
        with engine.connect() as connection:
            connection.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {_quote(name)}")
    finally:
        engine.dispose()


def _quote(name):
    assert_database_name(name)
    return f'"{name}"'


def _synthetic(database_name, owner_role):
    owner, base, material, version, index = (str(uuid4()) for _ in range(5))
    text = "synthetic span alpha"
    digest = hashlib.sha256(text.encode()).hexdigest()
    profile = "synthetic-embed:1024"
    snapshot = {
        "database_name": database_name,
        "database_owner_role": owner_role,
        "current_user": owner_role,
        "schema_revision": code_schema_revision(),
        "user_ids": [owner],
        "embedding_profile": profile,
        "knowledge_bases": [
            {"id": base, "name": "kb", "owner_user_id": owner, "deleted_at": None}
        ],
        "materials": [
            {
                "id": material,
                "owner_user_id": owner,
                "knowledge_base_id": base,
                "title": "Notes",
                "version_id": version,
                "source_sha256": digest,
                "is_current": True,
                "source_mime": "text/plain",
                "byte_size": len(text.encode()),
            }
        ],
        "indexes": [
            {
                "id": index,
                "material_version_id": version,
                "pipeline_profile": SEMANTIC_PROFILE,
                "embedding_profile": profile,
                "embedding_dim": EMBEDDING_DIM,
                "chunk_count": 1,
                "status": "ready",
                "is_active": True,
                "generation": 1,
                "actual_chunks": 1,
                "embedding_rows": 1,
                "model_profiles": [profile],
                "vector_dim_min": EMBEDDING_DIM,
                "vector_dim_max": EMBEDDING_DIM,
            }
        ],
    }
    scopes = {
        "kb": {
            "knowledge_base_id": base,
            "index_version_ids": [index],
            "intent": "qa",
            "answer_mode": "knowledge_only",
        }
    }
    manifest = build_manifest(
        snapshot=snapshot,
        scopes=scopes,
        documents={version: "doc-1"},
        material_hashes={"doc-1": digest},
        dataset_sha256="cd" * 32,
        materials_sha256="ef" * 32,
        embedding_model="synthetic-embed",
    )
    vector = [0.25 if item == 0 else 0.0 for item in range(EMBEDDING_DIM)]
    records = [
        {
            "document_id": "doc-1",
            "material_id": material,
            "material_version_id": version,
            "knowledge_base_id": base,
            "knowledge_base_name": "kb",
            "title": "Notes",
            "source_sha256": digest,
            "source_mime": "text/plain",
            "byte_size": len(text.encode()),
            "index_version_id": index,
            "chunk_count": 1,
            "chunks": [
                {
                    "ordinal": 0,
                    "text": text,
                    "text_sha256": digest,
                    "locator": {"line": 1},
                    "embedding": vector,
                }
            ],
        }
    ]
    return manifest, records, vector


def _reject_write(factory):
    with factory() as db:
        assert db.scalar(sa.text("SHOW default_transaction_read_only")) == "on"
        with pytest.raises(sa.exc.InternalError, match="read-only"):
            db.execute(sa.text("CREATE TABLE study.reuse_probe(id integer)"))


def _owner_can_turn_read_only_off(factory):
    """The startup GUC is not a read-only role. The database owner can disable it."""
    with factory.begin() as db:
        db.execute(sa.text("SET TRANSACTION READ WRITE"))
        assert db.scalar(sa.text("SHOW transaction_read_only")) == "off"
        db.execute(sa.text("CREATE TABLE study.owner_disabled_probe(id integer)"))
        db.execute(sa.text("DROP TABLE study.owner_disabled_probe"))


def test_failed_retain_drops_the_database(tmp_path):
    created = None
    with pytest.raises(RuntimeError, match="build failed"):
        with isolated_database(tmp_path, retain=True) as factory:
            with factory() as db:
                created = db.scalar(sa.text("SELECT current_database()"))
            assert_database_name(created)
            raise RuntimeError("build failed")
    assert created
    assert not _exists(created)


def test_retained_index_is_read_only_and_rejects_a_bad_revision(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("embedding API must not be called")

    monkeypatch.setattr("study_platform.services.models.embed_texts", forbidden)
    monkeypatch.setattr("study_platform.cli.evaluate_rag.embed_texts", forbidden)
    raw, url = _url()
    kept = None
    try:
        with isolated_database(tmp_path, retain=True) as factory:
            with factory() as db:
                kept = db.scalar(sa.text("SELECT current_database()"))
                owner_role = db.scalar(sa.text("SELECT current_user"))
            assert_database_name(kept)
            manifest, records, vector = _synthetic(kept, owner_role)
            import_index(factory, manifest, records)
            stored = read_snapshot(factory)
            verify_index_contents(
                manifest,
                stored,
                embedding_model="synthetic-embed",
                schema_revision=code_schema_revision(),
            )
            assert stored["schema_revision"] == code_schema_revision()
        assert _exists(kept)
        with retained_database(
            manifest,
            application_database="study_platform",
            shared_test_database=url.database,
            test_database_url_raw=raw,
        ) as factory:
            snapshot = read_snapshot(factory)
            verify_index_contents(
                manifest,
                snapshot,
                embedding_model="synthetic-embed",
                schema_revision=code_schema_revision(),
            )
            with factory() as db:
                found = db.execute(
                    sa.text(
                        """
                        SELECT c.text, vector_dims(e.embedding), e.embedding::text
                        FROM study.material_chunks AS c
                        JOIN study.chunk_embeddings AS e ON e.chunk_id = c.id
                        """
                    )
                ).one()
                assert found[0] == "synthetic span alpha"
                assert found[1] == EMBEDDING_DIM
                assert found[2].startswith("[0.25")
                assert snapshot["schema_revision"] == code_schema_revision()
            _reject_write(factory)
            with read_only_retrieval(os.environ["DATABASE_URL"]) as retrieval:
                _reject_write(retrieval)
                _owner_can_turn_read_only_off(retrieval)
            _owner_can_turn_read_only_off(factory)
            mismatched = dict(snapshot, schema_revision="0000_not_a_head")
            with pytest.raises(IndexReuseError, match="schema revision"):
                verify_index_contents(
                    manifest,
                    mismatched,
                    embedding_model="synthetic-embed",
                    schema_revision=code_schema_revision(),
                )
            assert vector[0] == 0.25 and len(vector) == EMBEDDING_DIM
    finally:
        if kept and _exists(kept):
            _drop(kept)


def _ensure_other_role():
    engine = _admin()
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(
                "DO $$ BEGIN "
                "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'study_eval_other') "
                "THEN CREATE ROLE study_eval_other NOLOGIN; END IF; "
                "END $$"
            )
    finally:
        engine.dispose()


def test_owner_and_name_boundaries_reject_before_reuse():
    raw, url = _url()
    with pytest.raises(IndexReuseError, match="database name"):
        reuse_target_url(raw, url.database)
    with pytest.raises(IndexReuseError, match="database name"):
        reuse_target_url(raw, "postgres")
    _ensure_other_role()
    foreign = new_database_name()
    engine = _admin()
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(
                f"CREATE DATABASE {_quote(foreign)} OWNER study_eval_other"
            )
    finally:
        engine.dispose()
    try:
        manifest, _records, _vector = _synthetic(foreign, "study_eval_other")
        with pytest.raises(IndexReuseError, match="database owner"):
            with retained_database(
                manifest,
                application_database="study_platform",
                shared_test_database=url.database,
                test_database_url_raw=raw,
            ):
                raise AssertionError("foreign owner must not be reused")
        with pytest.raises(IndexReuseError, match="application database"):
            with retained_database(
                manifest,
                application_database=foreign,
                shared_test_database=url.database,
                test_database_url_raw=raw,
            ):
                raise AssertionError("application database must not be reused")
    finally:
        if _exists(foreign):
            _drop(foreign)


def _batch_vector(text):
    digest = hashlib.sha256(text.encode()).digest()
    # Eighths survive the cache JSON round trip, so a resumed index matches a clean one.
    return [((digest[index % len(digest)] + index) % 8 + 1) / 8 for index in range(1024)]


def _embeddings(database_name):
    _raw, url = _url()
    engine = sa.create_engine(url.set(database=database_name), connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                sa.text(
                    "SELECT c.text_sha256, e.embedding::text "
                    "FROM study.material_chunks AS c "
                    "JOIN study.chunk_embeddings AS e ON e.chunk_id = c.id "
                    "ORDER BY c.ordinal"
                )
            ).all()
        return [(row[0], row[1]) for row in rows]
    finally:
        engine.dispose()


def test_resume_matches_a_clean_index_and_failure_drops_only_this_database(tmp_path, monkeypatch):
    """Deterministic vectors only. No embedding provider is contacted."""
    secret = "sk-SUPERSECRETVALUE"
    lines = [f"span {index} unique" for index in range(25)]
    materials = [
        {
            "id": "rfc-9110",
            "knowledge_base": "A",
            "title": "rfc",
            "format": "txt",
            "sections": lines,
        }
    ]
    dataset = tmp_path / "facts.jsonl"
    dataset.write_text("{}\n", encoding="utf-8")
    dataset.with_name("materials.json").write_text("[]", encoding="utf-8")
    calls = []
    fail_short = {"on": True}

    def embed(texts, diagnostics=None, context=None):
        calls.append(list(texts))
        if fail_short["on"] and len(texts) < 20:
            request = httpx.Request("POST", "https://example.test/v1/embeddings")
            response = httpx.Response(429, headers={"x-request-id": "req-12345678"}, request=request)
            raise APIStatusError(
                secret,
                response=response,
                body={"error": {"message": secret, "code": "Throttling.AllocationQuota"}},
            )
        if diagnostics is not None:
            diagnostics["usage"] = {"total_tokens": len(texts), "api_key": secret}
            diagnostics["usage_status"] = "known"
            diagnostics["elapsed_ms"] = 1
        return [_batch_vector(text) for text in texts]

    monkeypatch.setattr("study_platform.cli.evaluate_rag.embed_texts", embed)
    seen = []
    real = isolated_database

    @contextmanager
    def spy(output, *, retain=False):
        with real(output, retain=retain) as factory:
            with factory() as db:
                seen.append(db.scalar(sa.text("SELECT current_database()")))
            yield factory

    monkeypatch.setattr("study_platform.cli.evaluate_rag.isolated_database", spy)
    shared = _url()[1].database
    cache = tmp_path / "cache"
    failed_out = tmp_path / "failed"
    failed_out.mkdir()
    args = type(
        "Args",
        (),
        {
            "strategy": "baseline",
            "dataset": dataset,
            "retrieval_only": True,
            "metric_k": [10],
            "metrics_format": "json",
            "reuse_index": None,
            "retain_index": True,
            "export_index": None,
            "embedding_cache": cache,
        },
    )()
    kept = []
    try:
        with pytest.raises(EvaluationFailed):
            asyncio.run(live_evaluation(args, [], materials, failed_out))
        assert seen and not _exists(seen[0])
        assert _exists(shared)
        report = json.loads((failed_out / "report.json").read_text(encoding="utf-8"))
        assert report["status"] == "failed"
        assert report["index_retained"] is False
        assert report["indexed_documents"] == []
        assert [row["outcome"] for row in report["corpus_embedding"]] == ["embedded", "failed"]
        assert report["corpus_usage"]["failed_cost"] == "unknown"
        assert report["corpus_usage"]["currency_total"] == "unknown"
        assert secret not in json.dumps(report)
        assert calls[0] and len(calls[0]) == 20
        fail_short["on"] = False
        calls.clear()
        resumed_out = tmp_path / "resumed"
        resumed_out.mkdir()
        asyncio.run(live_evaluation(args, [], materials, resumed_out))
        kept.append(seen[-1])
        assert _exists(seen[-1])
        assert calls and calls[0] == lines[20:]
        resumed_report = json.loads((resumed_out / "report.json").read_text(encoding="utf-8"))
        assert resumed_report["indexed_documents"] == ["rfc-9110"]
        assert resumed_report["index_retained"] is True
        assert resumed_report["corpus_embedding"][0]["new_corpus_charge"] is False
        clean_out = tmp_path / "clean"
        clean_out.mkdir()
        args.embedding_cache = tmp_path / "other-cache"
        calls.clear()
        asyncio.run(live_evaluation(args, [], materials, clean_out))
        kept.append(seen[-1])
        assert _exists(kept[-1])
        assert len(calls) == 2
        assert _embeddings(kept[0]) == _embeddings(kept[1])
        assert secret not in json.dumps(resumed_report)
    finally:
        for name in kept:
            if _exists(name):
                _drop(name)


def _count_embeddings(database_name):
    _raw, url = _url()
    engine = sa.create_engine(url.set(database=database_name), connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            return connection.scalar(sa.text("SELECT count(*) FROM study.chunk_embeddings"))
    finally:
        engine.dispose()


def test_missing_materials_file_does_not_create_a_database(tmp_path, monkeypatch):
    dataset = tmp_path / "facts.jsonl"
    dataset.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "out"
    output.mkdir()
    args = type(
        "Args",
        (),
        {
            "strategy": "baseline",
            "dataset": dataset,
            "retrieval_only": True,
            "metric_k": [10],
            "metrics_format": "json",
            "reuse_index": None,
            "retain_index": True,
            "export_index": None,
            "embedding_cache": None,
        },
    )()
    monkeypatch.setattr(
        "study_platform.cli.evaluate_rag.embed_texts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("embed")),
    )
    before = _admin()
    try:
        with before.connect() as connection:
            names = set(
                connection.scalars(sa.text("SELECT datname FROM pg_database WHERE datname LIKE 'study_eval_%'"))
            )
    finally:
        before.dispose()
    with pytest.raises(EvaluationFailed):
        asyncio.run(live_evaluation(args, [], [], output))
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["failure"]["stage"] == "local_input"
    assert report["failure"]["failure_kind"] == "local_missing"
    assert report["failure"]["mapped_error_code"] is None
    assert report["index_retained"] is False
    after = _admin()
    try:
        with after.connect() as connection:
            later = set(
                connection.scalars(sa.text("SELECT datname FROM pg_database WHERE datname LIKE 'study_eval_%'"))
            )
    finally:
        after.dispose()
    assert later == names


def _ranked_vector(index):
    similarity = (95 - 2 * index) / 100
    ortho = math.sqrt(1 - similarity * similarity)
    vector = [0.0] * 1024
    vector[0] = similarity
    vector[2 + index] = ortho
    return vector


def test_reuse_generation_binds_citations_and_reads_top_24(tmp_path, monkeypatch):
    """Real retrieval and generation. Only the HTTP transport is mocked."""
    assert Settings.model_fields["retrieval_top_k"].default == 10
    assert Settings.model_fields["retrieval_candidate_k"].default == 20
    monkeypatch.setenv("RETRIEVAL_TOP_K", "24")
    monkeypatch.setenv("RETRIEVAL_CANDIDATE_K", "48")
    monkeypatch.setenv("EMBEDDING_API_KEY", "local-test-embedding")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "local-test-chat")
    get_settings.cache_clear()
    lines = [f"marker-{index:02d} unique span" for index in range(25)]
    materials = [
        {"id": "notes", "knowledge_base": "A", "title": "notes", "format": "txt", "sections": lines}
    ]
    dataset = tmp_path / "facts.jsonl"
    dataset.write_text("{}\n", encoding="utf-8")
    dataset.with_name("materials.json").write_text("[]", encoding="utf-8")
    query_vector = [1.0] + [0.0] * 1023
    http_calls = []

    def handmade(texts, diagnostics=None, context=None):
        if diagnostics is not None:
            diagnostics["usage"] = {"total_tokens": len(texts)}
            diagnostics["usage_status"] = "known"
            diagnostics["elapsed_ms"] = 1
        return [_ranked_vector(int(text.split()[0].split("-")[1])) for text in texts]

    monkeypatch.setattr("study_platform.cli.evaluate_rag.embed_texts", handmade)
    build = tmp_path / "build"
    build.mkdir()
    build_args = type(
        "Args",
        (),
        {
            "strategy": "baseline",
            "dataset": dataset,
            "retrieval_only": True,
            "metric_k": [10],
            "metrics_format": "json",
            "reuse_index": None,
            "retain_index": True,
            "export_index": None,
            "embedding_cache": None,
        },
    )()
    kept = []
    try:
        asyncio.run(live_evaluation(build_args, [], materials, build))
        manifest = json.loads((build / "index_manifest.json").read_text(encoding="utf-8"))
        kept.append(manifest["database_name"])
        before = _count_embeddings(manifest["database_name"])
        assert before == 25

        def handler(request):
            payload = json.loads(request.content.decode())
            http_calls.append(request.url.path)
            if request.url.path.endswith("/embeddings"):
                inputs = payload["input"]
                assert isinstance(inputs, list) and len(inputs) <= 2
                data = [
                    {"object": "embedding", "embedding": query_vector, "index": index}
                    for index, _text in enumerate(inputs)
                ]
                return httpx2.Response(
                    200,
                    json={
                        "object": "list",
                        "data": data,
                        "model": "qwen3.7-text-embedding-flash",
                        "usage": {"prompt_tokens": 4, "total_tokens": 4},
                    },
                )
            blob = json.dumps(payload.get("messages", []), ensure_ascii=False)
            if "只改写检索问题" in blob:
                content = json.dumps({"query": "查找 marker-00 的记载"}, ensure_ascii=False)
            else:
                content = "记载为 marker-00 unique span [S1]"
            return httpx2.Response(
                200,
                json={
                    "id": "chatcmpl-local",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "deepseek-flash",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                },
            )

        transport = httpx2.MockTransport(handler)

        def _install(cls):
            original = cls.__init__

            def init(self, *args, **kwargs):
                kwargs["transport"] = transport
                kwargs.pop("mounts", None)
                original(self, *args, **kwargs)

            monkeypatch.setattr(cls, "__init__", init)

        _install(httpx2.Client)
        _install(httpx2.AsyncClient)
        from langchain_openai.chat_models._client_utils import (
            _cached_async_httpx_client,
            _cached_sync_httpx_client,
        )

        _cached_async_httpx_client.cache_clear()
        _cached_sync_httpx_client.cache_clear()
        monkeypatch.setattr(
            "study_platform.cli.evaluate_rag.seed_corpus",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("seed")),
        )
        answer_out = tmp_path / "answers"
        answer_out.mkdir()
        answer_args = type(
            "Args",
            (),
            {
                "strategy": "rewrite",
                "dataset": dataset,
                "retrieval_only": False,
                "metric_k": [24],
                "metrics_format": "json",
                "reuse_index": build / "index_manifest.json",
                "retain_index": False,
                "export_index": None,
                "embedding_cache": None,
            },
        )()
        case = {
            "id": "q1",
            "fact_group": "markers",
            "split": "heldout",
            "question": "哪里记载了 marker-00",
            "knowledge_base": "A",
            "answerable": True,
            "expected_facts": [["marker-00 unique span"]],
            "support": [{"document": "notes", "quote": "marker-00 unique span"}],
        }
        asyncio.run(live_evaluation(answer_args, [case], materials, answer_out))
        report = json.loads((answer_out / "report.json").read_text(encoding="utf-8"))
        assert report["status"] == "completed"
        assert report["index_reuse"]["corpus_embedding_calls"] == 0
        assert report["index_retained"] is False
        row = report["cases"][0]
        assert row["answer_error"] is None
        assert row["answer"] == "记载为 marker-00 unique span [S1]"
        assert [item["text"] for item in row["evidence"]] == lines[:24]
        assert row["citations"] == [str(row["evidence"][0]["id"])]
        assert row["citation_valid"] is True
        costs = report["query_costs"]
        assert costs["currency_total"] == "unknown"
        assert costs["embedding"][0]["calls"] == 1
        assert costs["embedding"][0]["usage_status"] == "known"
        assert costs["rewrite"] and costs["rewrite"][0]["usage_status"] == "known"
        assert costs["generation"][0]["calls"] == 1
        assert costs["generation"][0]["usage_status"] == "known"
        assert sum(path.endswith("/embeddings") for path in http_calls) == 1
        assert sum(path.endswith("/chat/completions") for path in http_calls) == 2
        assert _count_embeddings(manifest["database_name"]) == before
        assert get_settings().retrieval_top_k == 24
        assert get_settings().retrieval_candidate_k == 48
        assert "local-test-embedding" not in json.dumps(report)
        assert "local-test-chat" not in json.dumps(report)
    finally:
        for name in kept:
            if _exists(name):
                _drop(name)
