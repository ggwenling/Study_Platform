import asyncio
import hashlib
import json
import stat
from contextlib import contextmanager

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError

from study_platform.cli import evaluate_rag
from study_platform.core.config import Settings, get_settings
from study_platform.core.errors import ApiError
from study_platform.services import eval_embedding_resume as resume
from study_platform.services.chunking import SEMANTIC_PROFILE
from study_platform.services.eval_embedding_resume import (
    CorpusEmbedError,
    embed_document_batches,
    prepare_embedding_cache,
    summarize_corpus_usage,
)
from study_platform.services.model_failure import public_failure
from study_platform.services.models import describe_model_failure, embed_texts, provider_error

SECRET = "sk-SUPERSECRETVALUE"
BODY_TEXT = "Bearer super-token and the full provider body"
REQUEST_ID = "req-12345678"


def _vector(text, dim=1024):
    digest = hashlib.sha256(text.encode()).digest()
    return [((digest[index % len(digest)] + index) % 251) / 251 or 0.1 for index in range(dim)]


def _status(code, *, provider_code=None, retry_after=None, request_id=REQUEST_ID):
    headers = {}
    if retry_after is not None:
        headers["retry-after"] = retry_after
    if request_id is not None:
        headers["x-request-id"] = request_id
    request = httpx.Request("POST", "https://example.test/v1/embeddings")
    response = httpx.Response(code, headers=headers, request=request)
    return APIStatusError(
        BODY_TEXT,
        response=response,
        body={
            "error": {
                "message": BODY_TEXT,
                "code": provider_code,
                "request_id": request_id,
                "api_key": SECRET,
            },
            "note": SECRET,
        },
    )


def _identity(texts, model="qwen3.7-text-embedding-flash"):
    return resume.batch_identity(
        source_sha256="ab" * 32,
        pipeline_profile=SEMANTIC_PROFILE,
        embedding_model=model,
        embedding_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        embedding_dim=1024,
        texts=texts,
    )


def test_provider_failures_keep_codes_and_drop_body_text():
    rate = describe_model_failure(_status(429, provider_code="Throttling.RateQuota", retry_after="2"))
    allocation = describe_model_failure(
        _status(429, provider_code="Throttling.AllocationQuota", retry_after="9")
    )
    server = describe_model_failure(_status(503, provider_code="InternalError"))
    timeout = describe_model_failure(APITimeoutError(httpx.Request("POST", "https://example.test/v1")))
    connection = describe_model_failure(
        APIConnectionError(message=BODY_TEXT, request=httpx.Request("POST", "https://example.test/v1"))
    )
    auth = describe_model_failure(_status(401, provider_code="InvalidApiKey"))
    assert rate["failure_kind"] == "http_429" and rate["retryable"] is True
    assert rate["provider_code"] == "Throttling.RateQuota"
    assert rate["mapped_error_code"] == "model_quota_unavailable"
    assert rate["request_id"] == REQUEST_ID and rate["retry_after_seconds"] == 2
    assert allocation["provider_code"] == "Throttling.AllocationQuota"
    assert allocation["retryable"] is False
    assert allocation["mapped_error_code"] == "model_quota_unavailable"
    assert server["failure_kind"] == "http_5xx" and server["mapped_error_code"] == "model_service_error"
    assert timeout["failure_kind"] == "timeout" and timeout["mapped_error_code"] == "model_timeout"
    assert connection["failure_kind"] == "connection" and connection["mapped_error_code"] == "model_timeout"
    assert auth["failure_kind"] == "auth" and auth["mapped_error_code"] == "model_auth_failed"
    rendered = json.dumps(
        [rate, allocation, server, timeout, connection, auth], ensure_ascii=False
    )
    assert SECRET not in rendered and BODY_TEXT not in rendered and "retry-after" not in rendered


def test_unparsed_retry_after_and_secret_request_id_are_not_copied():
    dirty = describe_model_failure(
        _status(429, provider_code="Throttling.RateQuota", retry_after="soon", request_id=SECRET)
    )
    long_wait = describe_model_failure(
        _status(503, provider_code=None, retry_after="31", request_id="req-12345678")
    )
    assert dirty["request_id"] is None and dirty["retry_after_unparsed"] is True
    assert dirty["retryable"] is False and dirty["retry_after_seconds"] is None
    assert long_wait["retry_after_seconds"] == 31 and long_wait["retryable"] is False
    assert SECRET not in json.dumps(dirty)


def test_embedding_validation_kinds_are_distinct(monkeypatch):
    settings = Settings(_env_file=None, embedding_api_key=SECRET)

    class FakeClient:
        def __init__(self, **kwargs):
            self.timeout = kwargs["timeout"]
            self.max_retries = kwargs["max_retries"]
            self.api_key = kwargs["api_key"]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def install(factory):
        monkeypatch.setattr("study_platform.services.models.OpenAI", factory)
        monkeypatch.setattr("study_platform.services.models.get_settings", lambda: settings)

    class CountClient(FakeClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

            class Embeddings:
                @staticmethod
                def create(**_kwargs):
                    class Usage:
                        def model_dump(self):
                            return {"total_tokens": 4, "api_key": SECRET, "note": BODY_TEXT}

                    class Response:
                        data = []
                        usage = Usage()

                    return Response()

            self.embeddings = Embeddings()

    install(CountClient)
    diagnostics = {}
    with pytest.raises(ApiError, match="数量或序号"):
        embed_texts(["alpha"], diagnostics=diagnostics)
    assert diagnostics["failure"]["failure_kind"] == "invalid_count"
    assert diagnostics["failure"]["http_status"] == 200
    assert diagnostics["failure"]["usage_status"] == "known"
    assert diagnostics["usage"] == {"total_tokens": 4}
    assert diagnostics["failure"]["error_code"] == "embedding_invalid"

    class ValueClient(FakeClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

            class Embeddings:
                @staticmethod
                def create(**_kwargs):
                    class Row:
                        index = 0
                        embedding = [float("nan")] * 1024

                    class Usage:
                        def model_dump(self):
                            return {"total_tokens": 1}

                    class Response:
                        data = [Row()]
                        usage = Usage()

                    return Response()

            self.embeddings = Embeddings()

    install(ValueClient)
    diagnostics = {}
    with pytest.raises(ApiError):
        embed_texts(["alpha"], diagnostics=diagnostics)
    assert diagnostics["failure"]["failure_kind"] == "invalid_value"

    class DimensionClient(FakeClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

            class Embeddings:
                @staticmethod
                def create(**_kwargs):
                    class Row:
                        index = 0
                        embedding = [0.2, 0.4]

                    class Response:
                        data = [Row()]
                        usage = None

                    return Response()

            self.embeddings = Embeddings()

    install(DimensionClient)
    captured = {}

    def spy(**kwargs):
        captured.update(kwargs)
        return DimensionClient(**kwargs)

    monkeypatch.setattr("study_platform.services.models.OpenAI", spy)
    diagnostics = {}
    with pytest.raises(ApiError, match="1024"):
        embed_texts(["alpha"], diagnostics=diagnostics)
    assert diagnostics["failure"]["failure_kind"] == "invalid_dimension"
    assert diagnostics["failure"]["usage_status"] == "unknown"
    assert captured["timeout"] == settings.model_timeout_seconds == 60
    assert captured["max_retries"] == 0
    rendered = json.dumps(diagnostics)
    assert SECRET not in rendered and BODY_TEXT not in rendered


def test_resume_fills_only_the_missing_batch_and_matches_a_clean_run(tmp_path):
    texts = [f"line-{index}" for index in range(25)]
    cache = tmp_path / "cache"
    calls = []

    def embed(batch, diagnostics=None, context=None):
        calls.append(list(batch))
        if batch == texts[20:]:
            raise _status(503, provider_code="InternalError", retry_after=None)
        if diagnostics is not None:
            diagnostics["usage"] = {"total_tokens": len(batch), "api_key": SECRET}
            diagnostics["usage_status"] = "known"
            diagnostics["elapsed_ms"] = 5
        return [_vector(text) for text in batch]

    with pytest.raises(CorpusEmbedError) as caught:
        embed_document_batches(
            texts,
            source_sha256="ab" * 32,
            pipeline_profile=SEMANTIC_PROFILE,
            document_id="rfc-9110",
            cache_dir=cache,
            embedding_model="qwen3.7-text-embedding-flash",
            embedding_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            embedding_dim=1024,
            pause=lambda _seconds: None,
            embed=embed,
        )
    assert [row["outcome"] for row in caught.value.metrics] == ["embedded", "failed"]
    assert caught.value.metrics[1]["batch_offset"] == 20
    assert caught.value.metrics[1]["usage_status"] == "unknown"
    assert caught.value.metrics[1]["new_corpus_charge"] is None
    summary = summarize_corpus_usage(caught.value.metrics)
    assert summary["accepted_new_tokens"] == 20
    assert summary["failed_cost"] == "unknown"
    assert summary["currency_total"] == "unknown"
    assert summary["partial"] is True
    assert SECRET not in json.dumps(caught.value.metrics)
    mode = (cache / "batches").stat().st_mode
    assert mode & 0o077 == 0
    stored = next((cache / "batches").glob("*.json"))
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600
    assert "line-0" not in stored.read_text(encoding="utf-8")

    calls.clear()

    def embed_rest(batch, diagnostics=None, context=None):
        calls.append(list(batch))
        if diagnostics is not None:
            diagnostics["usage"] = {"total_tokens": len(batch)}
            diagnostics["usage_status"] = "known"
            diagnostics["elapsed_ms"] = 4
        return [_vector(text) for text in batch]

    resumed, resumed_metrics = embed_document_batches(
        texts,
        source_sha256="ab" * 32,
        pipeline_profile=SEMANTIC_PROFILE,
        document_id="rfc-9110",
        cache_dir=cache,
        embedding_model="qwen3.7-text-embedding-flash",
        embedding_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        embedding_dim=1024,
        embed=embed_rest,
    )
    clean_calls = []

    def embed_all(batch, diagnostics=None, context=None):
        clean_calls.append(list(batch))
        if diagnostics is not None:
            diagnostics["usage"] = {"total_tokens": len(batch)}
            diagnostics["usage_status"] = "known"
        return [_vector(text) for text in batch]

    clean, _metrics = embed_document_batches(
        texts,
        source_sha256="ab" * 32,
        pipeline_profile=SEMANTIC_PROFILE,
        document_id="rfc-9110",
        cache_dir=None,
        embedding_model="qwen3.7-text-embedding-flash",
        embedding_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        embedding_dim=1024,
        embed=embed_all,
    )
    assert calls == [texts[20:]]
    assert [row["outcome"] for row in resumed_metrics] == ["cached", "embedded"]
    assert resumed_metrics[0]["new_corpus_charge"] is False and resumed_metrics[0]["calls"] == 0
    cached_summary = summarize_corpus_usage(resumed_metrics)
    assert cached_summary["cached_new_corpus_charge"] is False
    assert cached_summary["cached_tokens_not_charged_again"] == 20
    assert resumed == clean
    assert len(clean_calls) == 2


def test_mismatch_corrupt_cache_and_allocation_quota_do_not_pollute(tmp_path):
    texts = ["alpha"]
    cache = tmp_path / "cache"
    calls = []

    def embed(batch, diagnostics=None, context=None):
        calls.append((batch, diagnostics is not None))
        if diagnostics is not None:
            diagnostics["usage"] = {"total_tokens": 3}
            diagnostics["usage_status"] = "known"
            diagnostics["elapsed_ms"] = 1
        return [_vector(text) for text in batch]

    embed_document_batches(
        texts,
        source_sha256="ab" * 32,
        pipeline_profile=SEMANTIC_PROFILE,
        document_id="doc",
        cache_dir=cache,
        embedding_model="model-a",
        embedding_base_url="https://example.test/v1",
        embedding_dim=1024,
        embed=embed,
    )
    original = (cache / "batches").glob("*.json")
    original_name = next(original).name
    calls.clear()
    embed_document_batches(
        texts,
        source_sha256="cd" * 32,
        pipeline_profile=SEMANTIC_PROFILE,
        document_id="doc",
        cache_dir=cache,
        embedding_model="model-b",
        embedding_base_url="https://example.test/v1",
        embedding_dim=1024,
        embed=embed,
    )
    assert calls == [(["alpha"], True)]
    assert (cache / "batches" / original_name).is_file()
    cached_file = cache / "batches" / original_name
    cached_file.write_text("{", encoding="utf-8")
    calls.clear()
    embed_document_batches(
        texts,
        source_sha256="ab" * 32,
        pipeline_profile=SEMANTIC_PROFILE,
        document_id="doc",
        cache_dir=cache,
        embedding_model="model-a",
        embedding_base_url="https://example.test/v1",
        embedding_dim=1024,
        embed=embed,
    )
    assert calls == [(["alpha"], True)]
    pauses = []

    def quota(batch, diagnostics=None, context=None):
        raise _status(429, provider_code="Throttling.AllocationQuota", retry_after="1")

    with pytest.raises(CorpusEmbedError) as caught:
        embed_document_batches(
            ["beta"],
            source_sha256="ab" * 32,
            pipeline_profile=SEMANTIC_PROFILE,
            document_id="doc",
            cache_dir=cache,
            embedding_model="model-a",
            embedding_base_url="https://example.test/v1",
            embedding_dim=1024,
            pause=pauses.append,
            embed=quota,
        )
    assert pauses == []
    assert caught.value.failure["attempts"] == 1
    assert caught.value.metrics[0]["outcome"] == "failed"


def test_rate_quota_retries_are_bounded_and_invalid_requests_are_not(tmp_path):
    pauses = []
    attempts = {"count": 0}

    def flaky(batch, diagnostics=None, context=None):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise _status(429, provider_code="Throttling.RateQuota", retry_after="0")
        if diagnostics is not None:
            diagnostics["usage"] = {"total_tokens": 2}
            diagnostics["usage_status"] = "known"
        return [_vector(batch[0])]

    _vectors, metrics = embed_document_batches(
        ["gamma"],
        source_sha256="ab" * 32,
        pipeline_profile=SEMANTIC_PROFILE,
        document_id="doc",
        cache_dir=tmp_path / "cache",
        embedding_model="model-a",
        embedding_base_url="https://example.test/v1",
        embedding_dim=1024,
        pause=pauses.append,
        embed=flaky,
    )
    assert attempts["count"] == 3 and pauses == [0, 0]
    assert metrics[0]["prior_attempt_cost"] == "unknown"
    assert summarize_corpus_usage(metrics)["retry_failure_cost"] == "unknown"

    def bad_request(batch, diagnostics=None, context=None):
        attempts["bad"] = attempts.get("bad", 0) + 1
        raise _status(400, provider_code="InvalidParameter", retry_after=None)

    with pytest.raises(CorpusEmbedError) as caught:
        embed_document_batches(
            ["delta"],
            source_sha256="ab" * 32,
            pipeline_profile=SEMANTIC_PROFILE,
            document_id="doc",
            cache_dir=None,
            embedding_model="model-a",
            embedding_base_url="https://example.test/v1",
            embedding_dim=1024,
            pause=pauses.append,
            embed=bad_request,
        )
    assert attempts["bad"] == 1
    assert caught.value.failure["failure_kind"] == "invalid_request"
    assert caught.value.failure["mapped_error_code"] == provider_error(
        _status(400, provider_code="InvalidParameter")
    ).code


def test_loose_cache_directory_is_refused_before_embedding(tmp_path):
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(resume.CacheError):
        prepare_embedding_cache(public)
    link = tmp_path / "link"
    link.symlink_to(tmp_path)
    with pytest.raises(resume.CacheError):
        prepare_embedding_cache(link)


def test_copied_source_is_not_an_indexed_batch(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    settings = Settings(_env_file=None, storage_dir=storage)
    monkeypatch.setattr(evaluate_rag, "get_settings", lambda: settings)
    monkeypatch.setattr("study_platform.services.storage.get_settings", lambda: settings)
    monkeypatch.setattr("study_platform.services.ingestion.get_settings", lambda: settings)

    class Database:
        def add(self, _obj):
            return None

        def flush(self):
            return None

    class Factory:
        def begin(self):
            return self

        def __enter__(self):
            return Database()

        def __exit__(self, *_args):
            return False

    calls = {"count": 0}

    def embed(batch, diagnostics=None, context=None):
        calls["count"] += 1
        if calls["count"] == 1:
            if diagnostics is not None:
                diagnostics["usage"] = {"total_tokens": 1}
                diagnostics["usage_status"] = "known"
                diagnostics["elapsed_ms"] = 1
            return [_vector(batch[0])]
        raise _status(503, provider_code="InternalError.Unknown")

    monkeypatch.setattr(evaluate_rag, "embed_texts", embed)
    materials = [
        {"id": "nist-186-5", "knowledge_base": "A", "title": "one", "format": "txt", "sections": ["first line"]},
        {
            "id": "rfc-9110",
            "knowledge_base": "A",
            "title": "two",
            "format": "txt",
            "sections": ["UNIQUE-COPIED-NOT-INDEXED"],
        },
    ]
    with pytest.raises(CorpusEmbedError) as caught:
        evaluate_rag.seed_corpus(Factory(), materials, cache_dir=None)
    assert caught.value.indexed_documents == ["nist-186-5"]
    assert [row["document"] for row in caught.value.metrics] == ["nist-186-5", "rfc-9110"]
    assert [row["outcome"] for row in caught.value.metrics] == ["embedded", "failed"]
    assert "copied" not in json.dumps(caught.value.metrics)
    assert caught.value.metrics[1]["new_corpus_charge"] is None
    copied = list(storage.rglob("source"))
    assert copied and any(path.read_text(encoding="utf-8").strip() == "UNIQUE-COPIED-NOT-INDEXED" for path in copied)


def test_failed_report_is_failed_and_reuse_can_answer_without_corpus_embed(tmp_path, monkeypatch):
    secret_failure = describe_model_failure(_status(503, provider_code="InternalError"))
    dropped = {}

    @contextmanager
    def fake_database(_output, *, retain=False):
        try:
            yield object()
            dropped["succeeded"] = True
        except BaseException:
            dropped["succeeded"] = False
            raise
        finally:
            dropped["drop"] = evaluate_rag.should_drop_database(
                created=True, retain=retain, succeeded=dropped.get("succeeded", False)
            )

    def explode(*_args, **_kwargs):
        raise CorpusEmbedError(
            [
                {
                    "document": "nist-186-5",
                    "outcome": "embedded",
                    "usage": {"total_tokens": 8},
                    "usage_status": "known",
                    "new_corpus_charge": True,
                },
                {
                    "document": "rfc-9110",
                    "outcome": "failed",
                    "usage": None,
                    "usage_status": "unknown",
                    "new_corpus_charge": None,
                },
            ],
            secret_failure,
        )

    monkeypatch.setattr(evaluate_rag, "isolated_database", fake_database)
    monkeypatch.setattr(evaluate_rag, "seed_corpus", explode)
    output = tmp_path / "out"
    output.mkdir()
    args = _args(tmp_path, retrieval_only=False)
    with pytest.raises(evaluate_rag.EvaluationFailed) as caught:
        asyncio.run(evaluate_rag.live_evaluation(args, [{"id": "q"}], [], output))
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["failure"]["stage"] == "corpus_embedding"
    assert report["failure"]["http_status"] == 503
    assert report["failure"]["mapped_error_code"] == "model_service_error"
    assert report["corpus_usage"]["failed_cost"] == "unknown"
    assert report["corpus_usage"]["currency_total"] == "unknown"
    assert dropped["drop"] is True and dropped["succeeded"] is False
    text = evaluate_rag.evaluation_failure_text(caught.value.failure)
    rendered = text + json.dumps(report)
    assert "http_status=503" in text and "mapped_error_code=model_service_error" in text
    assert SECRET not in rendered and BODY_TEXT not in rendered
    assert public_failure({"failure_kind": "http_429", "request_id": SECRET})["request_id"] is None

    calls = []

    async def evaluate_case(*_args, **kwargs):
        calls.append(kwargs["retrieval_only"])
        return []

    monkeypatch.setattr(evaluate_rag, "seed_corpus", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("seed")))
    monkeypatch.setattr(evaluate_rag, "embed_texts", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("embed")))
    monkeypatch.setattr(evaluate_rag, "evaluate_case", evaluate_case)
    monkeypatch.setattr(evaluate_rag, "import_index", lambda *_a, **_k: calls.append("import"))
    monkeypatch.setattr(evaluate_rag, "read_snapshot", lambda *_a, **_k: _snapshot())
    monkeypatch.setattr(evaluate_rag, "read_only_retrieval", lambda *_a, **_k: _null())
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://localhost/base_test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/study_eval_" + "cd" * 16 + "_test")
    monkeypatch.setattr(evaluate_rag, "load_index_package", lambda _path: (_manifest(), [{"ok": True}]))
    monkeypatch.setattr(evaluate_rag, "assert_dataset_matches", lambda *_a, **_k: None)
    monkeypatch.setattr(evaluate_rag, "verify_index_contents", lambda *_a, **_k: None)
    monkeypatch.setattr(evaluate_rag, "assert_database_access", lambda *_a, **_k: None)
    answer_out = tmp_path / "answers"
    answer_out.mkdir()
    answer_args = _args(tmp_path, retrieval_only=False)
    answer_args.reuse_index = tmp_path / "package"
    asyncio.run(
        evaluate_rag.live_evaluation(
            answer_args, [{"id": "q", "knowledge_base": "kb"}], [], answer_out
        )
    )
    saved = json.loads((answer_out / "report.json").read_text(encoding="utf-8"))
    assert calls == ["import", False]
    assert saved["status"] == "completed"
    assert saved["index_reuse"]["corpus_embedding_calls"] == 0
    assert saved["corpus_embedding"] == []
    assert saved["query_costs"]["currency_total"] == "unknown"
    assert saved["indexed_documents"] == []


def _args(tmp_path, *, retrieval_only):
    dataset = tmp_path / "facts.jsonl"
    if not dataset.exists():
        dataset.write_text("{}\n", encoding="utf-8")
        dataset.with_name("materials.json").write_text("[]", encoding="utf-8")
    return type(
        "Args",
        (),
        {
            "strategy": "baseline",
            "dataset": dataset,
            "retrieval_only": retrieval_only,
            "metric_k": [10],
            "metrics_format": "json",
            "reuse_index": None,
            "retain_index": False,
            "export_index": None,
            "embedding_cache": None,
        },
    )()


def _snapshot():
    settings = get_settings()
    return {
        "database_name": "study_eval_" + "ab" * 16 + "_test",
        "schema_revision": evaluate_rag.code_schema_revision(),
        "embedding_model": settings.embedding_model,
    }


def _manifest():
    settings = get_settings()
    schema = evaluate_rag.code_schema_revision()
    return {
        "binding": "export",
        "schema_revision": schema,
        "embedding_model": settings.embedding_model,
        "embedding_dim": 1024,
        "scopes": [{"knowledge_base_name": "kb"}],
        "documents": {},
        "material_hashes": {},
        "eval_owner_id": "00000000-0000-4000-8000-000000000001",
    }


@contextmanager
def _null():
    yield None


def test_http_200_disconnect_is_not_a_timeout():
    request = httpx.Request("POST", "https://example.test/v1/embeddings")
    response = httpx.Response(200, request=request)
    failure = describe_model_failure(APIConnectionError(message=BODY_TEXT, request=request))
    response_failure = describe_model_failure(
        APIStatusError(BODY_TEXT, response=response, body={"error": {"message": SECRET}})
    )
    assert failure["failure_kind"] == "connection"
    assert response_failure["failure_kind"] == "http_200_incomplete"
    assert response_failure["failure_kind"] != "timeout"
    assert SECRET not in json.dumps(response_failure)
