"""Server-side model clients."""

import math
import time
from dataclasses import replace
from typing import Any

from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from study_platform.core.config import Settings, get_settings
from study_platform.core.errors import ApiError
from study_platform.services.model_connections import resolved
from study_platform.services.model_failure import (
    allow_usage,
    classify_model_exception,
    public_failure,
    retryable,
)
from study_platform.services.model_network import async_client, sync_client
from study_platform.services.model_usage import ModelUsageContext, record_model_call


def provider_error(exc: Exception) -> ApiError:
    # SDK connection errors wrap transport policy failures; preserve the safe reason.
    cause = exc
    while cause is not None:
        if isinstance(cause, ApiError):
            return cause
        cause = cause.__cause__
    if isinstance(exc, APIStatusError):
        status = exc.status_code
        if status in (401, 403):
            return ApiError(502, "model_auth_failed", "模型服务认证或权限不足，请检查服务配置")
        if status in (402, 429):
            return ApiError(503, "model_quota_unavailable", "模型额度、余额或速率受限，已停止本次调用")
        return ApiError(502, "model_service_error", "模型服务暂时不可用", retryable=status >= 500)
    if isinstance(exc, (APIConnectionError, APITimeoutError, TimeoutError)):
        return ApiError(504, "model_timeout", "模型服务连接超时", retryable=True)
    return ApiError(502, "model_response_invalid", "模型服务返回了无法使用的结果")


def chat_profile(*, use_fallback: bool, settings: Settings | None = None) -> tuple[str, str, str]:
    settings = settings or get_settings()
    if use_fallback:
        api_key = settings.chat_fallback_api_key.get_secret_value() or settings.deepseek_api_key.get_secret_value()
        base_url = settings.chat_fallback_base_url or settings.deepseek_base_url
        return base_url, api_key, settings.chat_fallback_model
    if not settings.deepseek_api_key.get_secret_value():
        raise ApiError(503, "model_not_configured", "尚未配置聊天模型")
    return settings.deepseek_base_url, settings.deepseek_api_key.get_secret_value(), settings.chat_model


def build_chat_model(
    *,
    use_fallback: bool = False,
    timeout: float | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    settings: Settings | None = None,
    connection: dict | None = None,
) -> ChatOpenAI:
    settings = settings or get_settings()
    if connection and connection.get("id"):
        base_url, api_key, model = connection["base_url"], connection["api_key"], connection["model"]
    else:
        base_url, api_key, model = chat_profile(use_fallback=use_fallback, settings=settings)
    if not api_key:
        raise ApiError(503, "model_not_configured", "尚未配置聊天模型")
    return ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout=settings.model_timeout_seconds if timeout is None else timeout,
        max_retries=0,
        max_tokens=settings.model_max_output_tokens if max_tokens is None else max_tokens,
        **({"temperature": temperature} if temperature is not None else {}),
        **({"http_client": sync_client(base_url, settings.model_timeout_seconds),
            "http_async_client": async_client(base_url, settings.model_timeout_seconds)}
           if connection and connection.get("id") else {"extra_body": {"thinking": {"type": "disabled"}}}),
        stream_usage=True,
    )


def chat_model(*, timeout: float | None = None, max_tokens: int | None = None,
               temperature: float | None = None) -> ChatOpenAI:
    return build_chat_model(timeout=timeout, max_tokens=max_tokens, temperature=temperature)


def describe_model_failure(exc: Exception, *, kind: str | None = None, http_status: int | None = None) -> dict:
    """Structured failure metadata. The provider message, body, and headers are not copied."""
    failure = classify_model_exception(exc)
    if kind:
        failure["failure_kind"] = kind
    if http_status is not None:
        failure["http_status"] = http_status
    if isinstance(exc, ApiError):
        failure["error_code"] = exc.code
        failure["mapped_error_code"] = exc.code
    else:
        failure["mapped_error_code"] = provider_error(exc).code
    failure["retryable"] = retryable(failure)
    return public_failure(failure)


def _note_embedding_failure(diagnostics: dict | None, exc: Exception, *, kind: str | None = None,
                            http_status: int | None = None) -> None:
    if diagnostics is None:
        return
    failure = describe_model_failure(exc, kind=kind, http_status=http_status)
    if diagnostics.get("usage_status") == "known":
        failure["usage_status"] = "known"
    diagnostics["failure"] = failure


def _embedding_usage(response: Any) -> dict[str, Any] | None:
    usage = getattr(response, "usage", None)
    return usage.model_dump() if usage is not None and hasattr(usage, "model_dump") else None


def embed_texts(
    texts: list[str],
    *,
    diagnostics: dict | None = None,
    context: ModelUsageContext | None = None,
) -> list[list[float]]:
    settings = get_settings()
    connection = resolved(context.owner_user_id, "embedding") if context else {"source": "platform"}
    binding = connection.get("binding") or {}
    model = connection.get("model") or binding.get("model") or settings.embedding_model
    base_url = connection.get("base_url") or binding.get("base_url") or settings.embedding_base_url
    api_key = connection.get("api_key") or settings.embedding_api_key.get_secret_value()
    if context:
        context = replace(context, connection_source=connection["source"])
    if not api_key:
        raise ApiError(503, "model_not_configured", "尚未配置向量模型")
    if not texts or len(texts) > 20:
        raise ValueError("Embedding batch must contain 1 to 20 texts")
    started = time.monotonic()
    if diagnostics is not None:
        diagnostics.update(calls=1, input_count=len(texts), usage=None)
    usage: dict[str, Any] | None = None
    success = False
    try:
        with OpenAI(
            api_key=api_key,
            base_url=base_url,
            **({"http_client": sync_client(base_url, settings.model_timeout_seconds)} if connection.get("id") else {}),
            timeout=settings.model_timeout_seconds,
            max_retries=0,
        ) as client:
            response = client.embeddings.create(
                model=model, input=texts,
                dimensions=settings.embedding_dimensions, encoding_format="float",
            )
        rows = sorted(response.data, key=lambda row: row.index)
        usage = _embedding_usage(response)
        safe_usage, usage_status = allow_usage(usage)
        if diagnostics is not None:
            diagnostics["usage"] = safe_usage
            diagnostics["usage_status"] = usage_status
        if [row.index for row in rows] != list(range(len(texts))):
            error = ApiError(502, "embedding_invalid", "向量响应数量或序号不正确。")
            _note_embedding_failure(diagnostics, error, kind="invalid_count", http_status=200)
            raise error
        vectors = [row.embedding for row in rows]
        if any(len(v) != 1024 for v in vectors):
            error = ApiError(502, "embedding_dimension_invalid", "向量连接必须输出 1024 维，请检查模型和 dimensions 参数。")
            _note_embedding_failure(diagnostics, error, kind="invalid_dimension", http_status=200)
            raise error
        if len(vectors) != len(texts) or any(
            not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in v) or not any(v)
            for v in vectors
        ):
            error = ApiError(502, "embedding_invalid", "向量模型返回维度或内容不正确")
            _note_embedding_failure(diagnostics, error, kind="invalid_value", http_status=200)
            raise error
        success = True
        return vectors
    except ApiError as exc:
        if diagnostics is None or "failure" not in diagnostics:
            _note_embedding_failure(diagnostics, exc)
        raise
    except Exception as exc:
        _note_embedding_failure(diagnostics, exc)
        raise provider_error(exc) from None
    finally:
        if diagnostics is not None:
            diagnostics["elapsed_ms"] = (time.monotonic() - started) * 1000
        record_model_call(
            context,
            model_name=model,
            used_fallback=False,
            elapsed_ms=(time.monotonic() - started) * 1000,
            success=success,
            usage=usage,
        )
