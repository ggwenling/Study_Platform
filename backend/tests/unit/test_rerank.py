from __future__ import annotations

import httpx
import pytest

from study_platform.core.errors import ApiError
from study_platform.services import rerank


def test_parse_rerank_and_dashscope_shapes():
    expected = [
        {"index": 1, "relevance_score": 0.91},
        {"index": 0, "relevance_score": 0.2},
    ]
    assert rerank.parse_results(
        {"results": [{"index": 0, "relevance_score": 0.2}, {"index": 1, "score": 0.91}]},
        2,
    ) == expected
    assert rerank.parse_results({"output": {"results": expected}}, 2) == expected


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"results": [{"index": 4, "relevance_score": 0.5}]},
        {"results": [{"index": 0, "relevance_score": "NaN"}]},
    ],
)
def test_parse_rerank_rejects_empty_or_invalid_scores(payload):
    with pytest.raises(ApiError, match="Rerank"):
        rerank.parse_results(payload, 2)


def test_post_uses_guarded_sync_client_and_standard_request(monkeypatch):
    seen = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"index": 0, "relevance_score": 0.8}]}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def post(self, url, **kwargs):
            seen.update(url=url, **kwargs)
            return Response()

    monkeypatch.setattr(rerank, "sync_client", lambda base, timeout: (seen.update(base=base, timeout=timeout), Client())[1])
    result = rerank._post_rerank(
        "https://rerank.example/v1", "secret", "jina-reranker", "query", ["doc"], 1, 8
    )
    assert result == [{"index": 0, "relevance_score": 0.8}]
    assert seen["base"] == "https://rerank.example/v1"
    assert seen["timeout"] == 8
    assert seen["url"] == "https://rerank.example/v1/rerank"
    assert seen["headers"] == {"Authorization": "Bearer secret"}
    assert seen["json"] == {
        "model": "jina-reranker", "query": "query", "documents": ["doc"], "top_n": 1,
    }


@pytest.mark.parametrize(
    ("base_url", "expected_endpoint"),
    [
        ("https://rerank.example/v1", "https://rerank.example/v1/rerank"),
        ("https://rerank.example/v1/", "https://rerank.example/v1/rerank"),
        ("https://rerank.example/v1/rerank", "https://rerank.example/v1/rerank"),
        (
            "https://dashscope.aliyuncs.com/compatible-api/v1/reranks",
            "https://dashscope.aliyuncs.com/compatible-api/v1/reranks",
        ),
        (
            "https://dashscope.aliyuncs.com/compatible-api/v1/reranks/",
            "https://dashscope.aliyuncs.com/compatible-api/v1/reranks",
        ),
    ],
)
def test_post_preserves_explicit_rerank_or_reranks_endpoint(monkeypatch, base_url, expected_endpoint):
    seen = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"output": {"results": [{"index": 0, "relevance_score": 0.9}]}}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def post(self, url, **kwargs):
            seen["url"] = url
            return Response()

    monkeypatch.setattr(rerank, "sync_client", lambda *_args: Client())
    result = rerank._post_rerank(base_url, "secret", "qwen3.7-text-rerank", "query", ["doc"], 1, 8)
    assert result == [{"index": 0, "relevance_score": 0.9}]
    assert seen["url"] == expected_endpoint


def test_timeout_returns_rrf_fallback_and_records_fallback_call(monkeypatch):
    usage = []
    monkeypatch.setattr(rerank, "resolved", lambda *_: {
        "id": "connection-id", "kind": "rerank", "revision": 1,
        "source": "connection:connection-id:1", "base_url": "https://api.example/v1",
        "model": "reranker", "api_key": "secret",
    })
    monkeypatch.setattr(
        rerank, "_post_rerank",
        lambda *args: (_ for _ in ()).throw(httpx.ReadTimeout("timed out")),
    )
    monkeypatch.setattr(rerank, "record_model_call", lambda context, **values: usage.append((context, values)))

    result = rerank.rerank(
        "user-id", "query", ["first", "second"], top_n=2,
        saved={"id": "connection-id", "kind": "rerank", "revision": 1}, fallback=True,
    )
    assert result is None
    context, record = usage[0]
    assert context.purpose == "rerank"
    assert context.connection_source == "connection:connection-id:1"
    assert record["used_fallback"] is True
    assert record["success"] is False
    assert record["usage"] == {"error_code": "model_timeout"}


def test_server_failure_is_reported_to_hit_test(monkeypatch):
    response = httpx.Response(500, request=httpx.Request("POST", "https://api.example/rerank"))
    failure = httpx.HTTPStatusError("server error", request=response.request, response=response)
    monkeypatch.setattr(rerank, "resolved", lambda *_: {
        "id": "connection-id", "kind": "rerank", "revision": 1,
        "source": "connection:connection-id:1", "base_url": "https://api.example/v1",
        "model": "reranker", "api_key": "secret",
    })
    monkeypatch.setattr(rerank, "_post_rerank", lambda *args: (_ for _ in ()).throw(failure))
    monkeypatch.setattr(rerank, "record_model_call", lambda *_args, **_kwargs: None)
    with pytest.raises(ApiError) as error:
        rerank.rerank(
            "user-id", "query", ["first"], top_n=1,
            saved={"id": "connection-id", "kind": "rerank", "revision": 1},
        )
    assert error.value.code == "rerank_service_error"
