import asyncio
import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from study_platform.cli import evaluate_rag
from study_platform.core.config import Settings, get_settings
from study_platform.persistence.models import ChunkEmbedding, IndexVersion, MaterialChunk
from study_platform.services import chat, eval_index_reuse
from study_platform.services.chunking import SEMANTIC_PROFILE
from study_platform.services.eval_index_reuse import (
    IndexReuseError,
    assert_database_access,
    assert_database_name,
    build_manifest,
    code_schema_revision,
    import_index,
    load_index_package,
    new_database_name,
    read_only_retrieval,
    retained_database,
    reuse_target_url,
    should_drop_database,
    validate_manifest_shape,
    verify_index_contents,
    write_package,
)


def _settings():
    get_settings.cache_clear()
    return Settings(_env_file=None)


def _snapshot(model="unit-embed", schema="0016_rerank_and_kb_retrieval"):
    owner, base, material, version, index = (str(uuid4()) for _ in range(5))
    digest = "ab" * 32
    profile = f"{model}:1024"
    return {
        "database_name": new_database_name(),
        "database_owner_role": "eval_role",
        "current_user": "eval_role",
        "schema_revision": schema,
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
                "byte_size": 8,
            }
        ],
        "indexes": [
            {
                "id": index,
                "material_version_id": version,
                "pipeline_profile": SEMANTIC_PROFILE,
                "embedding_profile": profile,
                "embedding_dim": 1024,
                "chunk_count": 1,
                "status": "ready",
                "is_active": True,
                "generation": 1,
                "actual_chunks": 1,
                "embedding_rows": 1,
                "model_profiles": [profile],
                "vector_dim_min": 1024,
                "vector_dim_max": 1024,
            }
        ],
        "_owner": owner,
        "_digest": digest,
        "_version": version,
        "_index": index,
    }


def _manifest(snapshot=None, **hashes):
    snapshot = snapshot or _snapshot(schema=code_schema_revision())
    scope = {
        "kb": {
            "knowledge_base_id": snapshot["knowledge_bases"][0]["id"],
            "index_version_ids": [snapshot["indexes"][0]["id"]],
            "intent": "qa",
            "answer_mode": "knowledge_only",
        }
    }
    return build_manifest(
        snapshot=snapshot,
        scopes=scope,
        documents={snapshot["_version"]: "doc-1"},
        material_hashes={"doc-1": snapshot["_digest"]},
        dataset_sha256=hashes.get("dataset_sha256", "cd" * 32),
        materials_sha256=hashes.get("materials_sha256", "ef" * 32),
        embedding_model=snapshot["embedding_profile"].rsplit(":", 1)[0],
    ), snapshot


def _records(manifest):
    material = manifest["materials"][0]
    index = manifest["indexes"][0]
    text = "widget-policy rule"
    return [
        {
            "document_id": material["document_id"],
            "material_id": material["material_id"],
            "material_version_id": material["version_id"],
            "knowledge_base_id": material["knowledge_base_id"],
            "knowledge_base_name": material["knowledge_base_name"],
            "title": material["title"],
            "source_sha256": material["source_sha256"],
            "source_mime": material["source_mime"],
            "byte_size": material["byte_size"],
            "index_version_id": index["id"],
            "chunk_count": 1,
            "chunks": [
                {
                    "ordinal": 0,
                    "text": text,
                    "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "locator": {"line": 1},
                    "embedding": [0.0] * 1024,
                }
            ],
        }
    ]


def test_should_drop_unless_a_successful_run_opts_in():
    assert should_drop_database(created=True, retain=False, succeeded=True)
    assert should_drop_database(created=True, retain=True, succeeded=False)
    assert should_drop_database(created=True, retain=False, succeeded=False)
    assert not should_drop_database(created=True, retain=True, succeeded=True)
    assert not should_drop_database(created=False, retain=True, succeeded=True)


def test_database_name_allowlist_and_owner_checks():
    assert_database_name(new_database_name())
    with pytest.raises(IndexReuseError, match="database name"):
        assert_database_name("study_platform")
    with pytest.raises(IndexReuseError, match="database name"):
        assert_database_name("postgres")
    with pytest.raises(IndexReuseError, match="database name"):
        assert_database_name("study_eval_test")
    name = new_database_name()
    access = {"database_name": name, "database_owner_role": "eval_role", "current_user": "eval_role"}
    assert_database_access(access, application_database="study_platform", shared_test_database="ci_test")
    with pytest.raises(IndexReuseError, match="application database"):
        assert_database_access(
            {**access, "database_name": name}, application_database=name, shared_test_database="ci_test"
        )
    with pytest.raises(IndexReuseError, match="shared test database"):
        assert_database_access(
            access, application_database="study_platform", shared_test_database=name
        )
    with pytest.raises(IndexReuseError, match="database owner"):
        assert_database_access(
            {**access, "current_user": "other_role"},
            application_database="study_platform",
            shared_test_database="ci_test",
        )


def test_reuse_url_uses_only_the_allowlisted_database_name():
    name = new_database_name()
    url = reuse_target_url("postgresql://localhost/base_test", name)
    assert url.database == name
    with pytest.raises(ValueError):
        reuse_target_url("postgresql://localhost/study_platform", name)
    with pytest.raises(ValueError):
        reuse_target_url("postgresql://localhost/base_test?dbname=study_platform", name)
    with pytest.raises(IndexReuseError):
        reuse_target_url("postgresql://localhost/base_test", "study_platform")


def test_manifest_roundtrip_and_mismatch_rejection():
    manifest, snapshot = _manifest()
    verify_index_contents(
        manifest,
        snapshot,
        embedding_model=manifest["embedding_model"],
        schema_revision=snapshot["schema_revision"],
    )
    changed = json.loads(json.dumps(snapshot))
    changed["schema_revision"] = "0015_note_lexical_search"
    with pytest.raises(IndexReuseError, match="schema revision"):
        verify_index_contents(
            manifest,
            changed,
            embedding_model=manifest["embedding_model"],
            schema_revision=manifest["schema_revision"],
        )
    changed = json.loads(json.dumps(snapshot))
    changed["embedding_profile"] = "other-model:1024"
    changed["indexes"][0]["embedding_profile"] = "other-model:1024"
    changed["indexes"][0]["model_profiles"] = ["other-model:1024"]
    with pytest.raises(IndexReuseError, match="embedding model"):
        verify_index_contents(
            manifest,
            changed,
            embedding_model=manifest["embedding_model"],
            schema_revision=manifest["schema_revision"],
        )
    changed = json.loads(json.dumps(snapshot))
    changed["indexes"][0]["embedding_dim"] = 768
    changed["indexes"][0]["vector_dim_min"] = 768
    changed["indexes"][0]["vector_dim_max"] = 768
    with pytest.raises(IndexReuseError, match="index version"):
        verify_index_contents(
            manifest,
            changed,
            embedding_model=manifest["embedding_model"],
            schema_revision=manifest["schema_revision"],
        )
    changed = json.loads(json.dumps(snapshot))
    changed["materials"][0]["source_sha256"] = "12" * 32
    with pytest.raises(IndexReuseError, match="material hash"):
        verify_index_contents(
            manifest,
            changed,
            embedding_model=manifest["embedding_model"],
            schema_revision=manifest["schema_revision"],
        )
    changed = json.loads(json.dumps(snapshot))
    changed["user_ids"] = [str(uuid4())]
    with pytest.raises(IndexReuseError, match="owner"):
        verify_index_contents(
            manifest,
            changed,
            embedding_model=manifest["embedding_model"],
            schema_revision=manifest["schema_revision"],
        )
    changed = json.loads(json.dumps(snapshot))
    changed["indexes"][0]["is_active"] = False
    with pytest.raises(IndexReuseError, match="index version"):
        verify_index_contents(
            manifest,
            changed,
            embedding_model=manifest["embedding_model"],
            schema_revision=manifest["schema_revision"],
        )


def test_pipeline_profile_must_match_on_export_import_and_reuse():
    manifest, snapshot = _manifest()
    assert manifest["pipeline_profile"] == SEMANTIC_PROFILE
    validate_manifest_shape(manifest)
    verify_index_contents(
        manifest, snapshot,
        embedding_model=manifest["embedding_model"], schema_revision=manifest["schema_revision"],
    )

    unknown = json.loads(json.dumps(manifest))
    unknown["pipeline_profile"] = "semantic-v5"
    unknown["indexes"][0]["pipeline_profile"] = "semantic-v5"
    with pytest.raises(IndexReuseError, match="pipeline profile"):
        validate_manifest_shape(unknown)

    mixed = json.loads(json.dumps(manifest))
    mixed["indexes"][0]["pipeline_profile"] = "semantic-v4"
    with pytest.raises(IndexReuseError, match="pipeline profile"):
        validate_manifest_shape(mixed)

    v4_snapshot = json.loads(json.dumps(snapshot))
    v4_snapshot["indexes"][0]["pipeline_profile"] = "semantic-v4"
    v4_manifest, _ = _manifest(v4_snapshot)
    assert v4_manifest["pipeline_profile"] == "semantic-v4"
    validate_manifest_shape(v4_manifest)
    with pytest.raises(IndexReuseError, match="pipeline profile"):
        verify_index_contents(
            v4_manifest, snapshot,
            embedding_model=manifest["embedding_model"], schema_revision=manifest["schema_revision"],
        )
    with pytest.raises(IndexReuseError, match="pipeline profile"):
        verify_index_contents(
            manifest, v4_snapshot,
            embedding_model=manifest["embedding_model"], schema_revision=manifest["schema_revision"],
        )
    mixed_snapshot = json.loads(json.dumps(snapshot))
    second_id = str(uuid4())
    mixed_snapshot["indexes"].append({
        **mixed_snapshot["indexes"][0], "id": second_id, "pipeline_profile": "semantic-v4",
    })
    with pytest.raises(IndexReuseError, match="pipeline profile"):
        build_manifest(
            snapshot=mixed_snapshot,
            scopes={
                "kb": {
                    "knowledge_base_id": mixed_snapshot["knowledge_bases"][0]["id"],
                    "index_version_ids": sorted([mixed_snapshot["indexes"][0]["id"], second_id]),
                    "intent": "qa",
                    "answer_mode": "knowledge_only",
                }
            },
            documents={mixed_snapshot["_version"]: "doc-1"},
            material_hashes={"doc-1": mixed_snapshot["_digest"]},
            dataset_sha256="cd" * 32,
            materials_sha256="ef" * 32,
            embedding_model=mixed_snapshot["embedding_profile"].rsplit(":", 1)[0],
        )


def test_import_keeps_the_manifest_profile():
    manifest, _snapshot_value = _manifest()
    exported = dict(manifest, binding="export")

    class Session:
        def __init__(self):
            self.added = []

        def add(self, obj):
            self.added.append(obj)

        def flush(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def load(body):
        session = Session()

        class Bound:
            def begin(self):
                return session

        import_index(Bound(), body, _records(body))
        return [obj for obj in session.added if isinstance(obj, IndexVersion)]

    assert load(exported)[0].pipeline_profile == SEMANTIC_PROFILE
    v4 = json.loads(json.dumps(exported))
    v4["pipeline_profile"] = "semantic-v4"
    v4["indexes"][0]["pipeline_profile"] = "semantic-v4"
    assert load(v4)[0].pipeline_profile == "semantic-v4"


def test_package_hash_and_secret_fields_are_rejected(tmp_path):
    manifest, _snapshot_value = _manifest()
    exported = dict(manifest, binding="export")
    package = tmp_path / "index"
    write_package(package, exported, _records(exported))
    loaded, records = load_index_package(package)
    assert loaded["binding"] == "export" and len(records) == 1
    digest = package / "package.sha256"
    digest.write_text("0" * 64 + "\n", encoding="utf-8")
    with pytest.raises(IndexReuseError, match="package hash"):
        load_index_package(package)
    tainted = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    tainted["api_key"] = "should-not-travel"
    (package / "manifest.json").write_text(json.dumps(tainted), encoding="utf-8")
    with pytest.raises(IndexReuseError, match="forbidden credential field"):
        load_index_package(package)


def test_import_inserts_stored_vectors_without_calling_embed(monkeypatch):
    manifest, _snapshot_value = _manifest()
    exported = dict(manifest, binding="export")
    records = _records(exported)

    def explode(*_args, **_kwargs):
        raise AssertionError("import must not embed, seed, or extract")

    monkeypatch.setattr("study_platform.services.models.embed_texts", explode)
    monkeypatch.setattr("study_platform.services.ingestion.extract", explode)
    monkeypatch.setattr(evaluate_rag, "seed_corpus", explode)
    monkeypatch.setattr(evaluate_rag, "render_source", explode)

    class Session:
        def __init__(self):
            self.added = []

        def add(self, obj):
            self.added.append(obj)

        def flush(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Factory:
        def __init__(self):
            self.session = Session()

        def begin(self):
            return self.session

    factory = Factory()
    import_index(factory, exported, records)
    embeddings = [obj for obj in factory.session.added if isinstance(obj, ChunkEmbedding)]
    chunks = [obj for obj in factory.session.added if isinstance(obj, MaterialChunk)]
    assert embeddings[0].embedding == [0.0] * 1024
    assert embeddings[0].model_profile_id == exported["embedding_profile"]
    assert chunks[0].lexical is not None
    assert chunks[0].text == "widget-policy rule"


class _Admin:
    def __init__(self, statements, *, current="postgres", can_create=True):
        self.statements = statements
        self.current = current
        self.can_create = can_create

    def connect(self):
        admin = self

        class Connection:
            dialect = SimpleNamespace(
                identifier_preparer=SimpleNamespace(quote=static_quote)
            )

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def scalar(self, sql):
                text = str(sql)
                if "current_database" in text:
                    return admin.current
                if "rolcreatedb" in text:
                    return admin.can_create
                raise AssertionError(text)

            def exec_driver_sql(self, sql):
                admin.statements.append(sql)

        return Connection()

    def dispose(self):
        return None


def static_quote(name):
    return '"' + name + '"'


class _Factory:
    def __init__(self, name):
        self.kw = {"bind": SimpleNamespace(dispose=lambda: None)}
        self.name = name

    def __call__(self):
        factory = self

        class Database:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def scalar(self, _sql):
                return factory.name

        return Database()


def _patch_isolation(monkeypatch, name, statements, **admin_kwargs):
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://localhost/base_test")
    monkeypatch.setattr(evaluate_rag, "new_database_name", lambda: name)
    monkeypatch.setattr(evaluate_rag.command, "upgrade", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(evaluate_rag, "session_factory", lambda: _Factory(name))
    monkeypatch.setattr(
        evaluate_rag.sa, "create_engine", lambda *_args, **_kwargs: _Admin(statements, **admin_kwargs)
    )


def test_failed_retained_build_still_drops_the_database(tmp_path, monkeypatch):
    name = new_database_name()
    statements = []
    _patch_isolation(monkeypatch, name, statements)

    with pytest.raises(RuntimeError, match="boom"):
        with evaluate_rag.isolated_database(tmp_path, retain=True):
            raise RuntimeError("boom")
    assert any(name in statement and statement.startswith("DROP DATABASE") for statement in statements)


def test_successful_default_drops_and_retain_keeps(tmp_path, monkeypatch):
    name = new_database_name()
    dropped = []
    _patch_isolation(monkeypatch, name, dropped)
    with evaluate_rag.isolated_database(tmp_path, retain=False):
        pass
    assert any(statement.startswith("DROP DATABASE") for statement in dropped)
    kept = []
    _patch_isolation(monkeypatch, name, kept)
    with evaluate_rag.isolated_database(tmp_path, retain=True):
        pass
    assert not any(statement.startswith("DROP DATABASE") for statement in kept)


def test_create_permission_failure_does_not_drop(tmp_path, monkeypatch):
    statements = []
    _patch_isolation(monkeypatch, new_database_name(), statements, can_create=False)
    with pytest.raises(ValueError, match="CREATEDB"):
        with evaluate_rag.isolated_database(tmp_path):
            pass
    assert statements == []


def test_read_only_retrieval_restores_the_chat_factory(monkeypatch):
    created = {}

    def fake_engine(url, **kwargs):
        created["url"] = url
        created["options"] = kwargs["connect_args"]["options"]
        return SimpleNamespace(dispose=lambda: created.setdefault("disposed", True))

    monkeypatch.setattr(eval_index_reuse.sa, "create_engine", fake_engine)
    previous = chat.session_factory
    with read_only_retrieval("postgresql+psycopg://localhost/study_eval_" + "ab" * 16 + "_test"):
        assert chat.session_factory is not previous
    assert chat.session_factory is previous
    assert created["disposed"] is True
    assert "default_transaction_read_only=on" in created["options"]


def test_retained_database_never_drops_and_rejects_a_foreign_owner(monkeypatch):
    manifest, snapshot = _manifest()
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://localhost/base_test")
    events = []

    class Engine:
        def connect(self):
            return nullcontext(object())

        def dispose(self):
            events.append("dispose")

    monkeypatch.setattr(eval_index_reuse.sa, "create_engine", lambda *_args, **_kwargs: Engine())
    monkeypatch.setattr(
        eval_index_reuse,
        "read_access",
        lambda _connection: {
            "database_name": snapshot["database_name"],
            "database_owner_role": "eval_role",
            "current_user": "intruder",
        },
    )
    previous = chat.session_factory
    with pytest.raises(IndexReuseError, match="database owner"):
        with retained_database(manifest, application_database="study_platform", shared_test_database="base_test"):
            raise AssertionError("must not yield")
    assert chat.session_factory is previous
    assert events == ["dispose"]

    monkeypatch.setattr(
        eval_index_reuse,
        "read_access",
        lambda _connection: {
            "database_name": snapshot["database_name"],
            "database_owner_role": "eval_role",
            "current_user": "eval_role",
        },
    )
    with pytest.raises(RuntimeError, match="stop"):
        with retained_database(manifest, application_database="study_platform", shared_test_database="base_test"):
            raise RuntimeError("stop")
    assert chat.session_factory is previous
    assert "DROP" not in "".join(events)


def test_retained_database_refuses_the_application_name_before_connecting(monkeypatch):
    manifest, _snapshot_value = _manifest()

    def explode(*_args, **_kwargs):
        raise AssertionError("must not connect")

    monkeypatch.setattr(eval_index_reuse.sa, "create_engine", explode)
    with pytest.raises(IndexReuseError, match="application database"):
        with retained_database(
            manifest,
            application_database=manifest["database_name"],
            shared_test_database="base_test",
        ):
            pass


def _package(tmp_path, manifest):
    exported = dict(manifest, binding="export")
    directory = tmp_path / "pkg"
    write_package(directory, exported, _records(exported))
    return directory


def test_reuse_does_not_seed_or_embed_and_rejects_a_stale_revision(tmp_path, monkeypatch):
    settings = get_settings()
    snapshot = _snapshot(model=settings.embedding_model, schema=code_schema_revision())
    manifest, snapshot = _manifest(snapshot)
    dataset = tmp_path / "facts.jsonl"
    dataset.write_text('{"id":"q"}\n', encoding="utf-8")
    dataset.with_name("materials.json").write_text("[]", encoding="utf-8")
    manifest["dataset_sha256"] = eval_index_reuse.file_sha256(dataset)
    manifest["materials_sha256"] = eval_index_reuse.file_sha256(dataset.with_name("materials.json"))
    package = _package(tmp_path, manifest)
    calls = []

    def explode(*_args, **_kwargs):
        raise AssertionError("reuse must not seed or embed")

    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://localhost/base_test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/study_eval_" + "cd" * 16 + "_test")
    monkeypatch.setattr(evaluate_rag, "seed_corpus", explode)
    monkeypatch.setattr(evaluate_rag, "embed_texts", explode)
    monkeypatch.setattr("study_platform.services.models.embed_texts", explode)
    monkeypatch.setattr(evaluate_rag, "isolated_database", lambda *_args, **_kwargs: nullcontext(object()))
    monkeypatch.setattr(evaluate_rag, "import_index", lambda *_args, **_kwargs: calls.append("import"))
    monkeypatch.setattr(evaluate_rag, "read_snapshot", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr(evaluate_rag, "read_only_retrieval", lambda *_args, **_kwargs: nullcontext(None))

    async def eval_case(*_args, **_kwargs):
        calls.append("retrieve")
        return []

    monkeypatch.setattr(evaluate_rag, "evaluate_case", eval_case)
    args = SimpleNamespace(
        strategy="baseline",
        dataset=dataset,
        retrieval_only=True,
        metric_k=[1, 10],
        metrics_format="json",
        reuse_index=package,
        retain_index=False,
        export_index=None,
    )
    output = tmp_path / "out"
    output.mkdir()
    asyncio.run(evaluate_rag.live_evaluation(args, [{"id": "q", "knowledge_base": "kb"}], [], output))
    assert calls == ["import", "retrieve"]
    saved = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert saved["index_reuse"]["corpus_embedding_calls"] == 0
    assert saved["corpus_embedding"] == []

    stale = json.loads(json.dumps(manifest))
    stale["schema_revision"] = "0015_note_lexical_search"
    stale_dir = tmp_path / "stale"
    write_package(stale_dir, dict(stale, binding="export"), _records(dict(stale, binding="export")))
    monkeypatch.setattr(
        evaluate_rag, "isolated_database", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("db"))
    )
    args.reuse_index = stale_dir
    with pytest.raises(evaluate_rag.EvaluationFailed) as stale_failure:
        asyncio.run(evaluate_rag.live_evaluation(args, [{"id": "q", "knowledge_base": "kb"}], [], tmp_path / "out2"))
    assert stale_failure.value.failure["failure_kind"] == "local_verification"
    assert stale_failure.value.failure["error_code"] == "schema_revision"
    assert stale_failure.value.failure["mapped_error_code"] is None


def test_reuse_rejects_a_different_dataset_before_opening_a_database(tmp_path, monkeypatch):
    manifest, _snapshot_value = _manifest()
    dataset = tmp_path / "facts.jsonl"
    dataset.write_text("one\n", encoding="utf-8")
    dataset.with_name("materials.json").write_text("[]", encoding="utf-8")
    manifest["dataset_sha256"] = eval_index_reuse.file_sha256(dataset)
    manifest["materials_sha256"] = eval_index_reuse.file_sha256(dataset.with_name("materials.json"))
    package = _package(tmp_path, manifest)
    dataset.write_text("two\n", encoding="utf-8")
    monkeypatch.setattr(
        evaluate_rag, "isolated_database", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("db"))
    )
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://localhost/base_test")
    args = SimpleNamespace(
        strategy="baseline",
        dataset=dataset,
        retrieval_only=True,
        metric_k=[10],
        metrics_format="json",
        reuse_index=package,
        retain_index=False,
        export_index=None,
    )
    with pytest.raises(evaluate_rag.EvaluationFailed) as mismatch:
        asyncio.run(evaluate_rag.live_evaluation(args, [], [], tmp_path / "out"))
    assert mismatch.value.failure["failure_kind"] == "local_verification"
    assert mismatch.value.failure["error_code"] == "dataset_hash"
    assert mismatch.value.failure["mapped_error_code"] is None
    assert mismatch.value.failure["stage"] == "local_input"


def test_dry_package_validation_makes_no_provider_call(tmp_path, monkeypatch, capsys):
    manifest, _snapshot_value = _manifest()
    package = _package(tmp_path, manifest)

    def explode(*_args, **_kwargs):
        raise AssertionError("dry validation must not connect or embed")

    monkeypatch.setattr(evaluate_rag, "isolated_database", explode)
    monkeypatch.setattr(evaluate_rag, "embed_texts", explode)
    monkeypatch.setattr("sys.argv", ["study-eval-rag", "--reuse-index", str(package)])
    evaluate_rag.main()
    assert "No database or provider calls" in capsys.readouterr().out


def test_live_hybrid_with_default_weight_does_not_open_a_database(monkeypatch, capsys):
    def explode(*_args, **_kwargs):
        raise AssertionError("invalid hybrid comparison must not connect")

    monkeypatch.setattr(evaluate_rag, "isolated_database", explode)
    monkeypatch.setattr(evaluate_rag, "embed_texts", explode)
    monkeypatch.setattr(evaluate_rag, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(evaluate_rag, "get_settings", lambda: Settings(_env_file=None, retrieval_lexical_weight=0.0))
    dataset = str(Path(__file__).resolve().parents[2] / "evaluation" / "retrieval_mini.jsonl")
    monkeypatch.setattr(
        "sys.argv",
        [
            "study-eval-rag",
            "--dataset",
            dataset,
            "--strategy",
            "hybrid",
            "--live",
            "--retrieval-only",
            "--output",
            "unused",
        ],
    )
    with pytest.raises(SystemExit, match="RETRIEVAL_LEXICAL_WEIGHT"):
        evaluate_rag.main()
    assert capsys.readouterr().err == ""


def test_code_schema_revision_is_the_single_alembic_head():
    assert code_schema_revision() == "0016_rerank_and_kb_retrieval"


def test_candidate_pool_is_the_max_of_candidate_k_and_top_k(monkeypatch):
    monkeypatch.setattr(
        chat,
        "get_settings",
        lambda: Settings(_env_file=None, retrieval_candidate_k=20, retrieval_top_k=24),
    )
    assert chat._candidate_limit() == 24
    monkeypatch.setattr(
        chat,
        "get_settings",
        lambda: Settings(_env_file=None, retrieval_candidate_k=20, retrieval_top_k=10),
    )
    assert chat._candidate_limit() == 20
