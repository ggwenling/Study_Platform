"""HTTP client for OpenAI-compatible `/rerank` endpoints."""

from __future__ import annotations

import math
import time
from dataclasses import replace
from typing import Any
from uuid import UUID

import httpx

from study_platform.core.config import get_settings
from study_platform.core.errors import ApiError
from study_platform.services.model_connections import resolved
from study_platform.services.model_network import sync_client
from study_platform.services.model_usage import ModelUsageContext, record_model_call


def rerank_payload(model: str, query: str, documents: list[str], top_n: int) -> dict[str, Any]:
    return {"model": model, "query": query, "documents": documents, "top_n": top_n}


def parse_results(payload: Any, document_count: int) -> list[dict[str, float | int]]:
    raw = payload
    if isinstance(raw, dict) and isinstance(raw.get("output"), dict):
        raw = raw["output"]
    if isinstance(raw, dict):
        raw = raw.get("results")
    if not isinstance(raw, list):
        raise ApiError(502, "rerank_response_invalid", "Rerank 返回格式不正确。")

    results = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        score = item.get("relevance_score", item.get("score"))
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < document_count:
            continue
        try:
            score = float(score)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(score) or index in seen:
            continue
        seen.add(index)
        results.append({"index": index, "relevance_score": score})
    if not results:
        raise ApiError(502, "rerank_response_invalid", "Rerank 没有返回有效排序结果。")
    return sorted(results, key=lambda item: (-item["relevance_score"], item["index"]))


def _post_rerank(base_url, api_key, model, query, documents, top_n, timeout):
    endpoint = f"{base_url.rstrip('/')}/rerank"
    with sync_client(base_url, timeout) as client:
        response = client.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
            json=rerank_payload(model, query, documents, top_n),
        )
    response.raise_for_status()
    return parse_results(response.json(), len(documents))


def rerank(
    owner_user_id: UUID,
    query: str,
    documents: list[str],
    *,
    top_n: int,
    saved: dict | None = None,
    fallback: bool = False,
) -> list[dict[str, float | int]] | None:
    """Rerank text documents, optionally recording that the caller used RRF fallback."""
    if not documents:
        return []
    context = ModelUsageContext(owner_user_id, "rerank")
    started = time.monotonic()
    success = False
    connection = None
    model_name = "rerank"
    usage = None
    try:
        connection = resolved(owner_user_id, "rerank", saved)
        if not connection.get("id"):
            raise ApiError(409, "rerank_not_configured", "请先配置并测试 Rerank 连接。")
        context = replace(context, connection_source=connection["source"])
        model_name = connection["model"]
        if not query.strip() or not 1 <= top_n <= len(documents):
            raise ApiError(422, "rerank_request_invalid", "Rerank 请求参数不正确。")
        timeout = get_settings().rerank_timeout_seconds
        results = _post_rerank(
            connection["base_url"], connection["api_key"], model_name,
            query, documents, top_n, timeout,
        )
        success = True
        return results[:top_n]
    except ApiError as error:
        usage = {"error_code": error.code}
        if fallback:
            return None
        raise
    except httpx.TimeoutException:
        usage = {"error_code": "model_timeout"}
        if fallback:
            return None
        raise ApiError(504, "model_timeout", "Rerank 服务响应超时。", retryable=True) from None
    except httpx.HTTPStatusError as error:
        usage = {"error_code": f"rerank_http_{error.response.status_code}"}
        if fallback:
            return None
        raise ApiError(502, "rerank_service_error", "Rerank 服务请求失败。", retryable=True) from None
    except Exception as error:
        mapped = ApiError(502, "rerank_service_error", "Rerank 服务暂时不可用。", retryable=True)
        usage = {"error_code": mapped.code}
        if fallback:
            return None
        raise mapped from error
    finally:
        record_model_call(
            context,
            model_name=model_name,
            used_fallback=fallback and not success,
            elapsed_ms=(time.monotonic() - started) * 1000,
            success=success,
            usage=usage,
        )
