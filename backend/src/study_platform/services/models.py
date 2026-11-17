"""Server-side model clients."""

import math
import time
from typing import Any

from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from study_platform.core.config import Settings, get_settings
from study_platform.core.errors import ApiError
from study_platform.services.model_usage import ModelUsageContext, record_model_call


def provider_error(exc: Exception) -> ApiError:
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
) -> ChatOpenAI:
    settings = settings or get_settings()
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
        extra_body={"thinking": {"type": "disabled"}},
        stream_usage=True,
    )


def chat_model(*, timeout: float | None = None, max_tokens: int | None = None,
               temperature: float | None = None) -> ChatOpenAI:
    return build_chat_model(timeout=timeout, max_tokens=max_tokens, temperature=temperature)


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
    if not settings.embedding_api_key.get_secret_value():
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
            api_key=settings.embedding_api_key.get_secret_value(),
            base_url=settings.embedding_base_url,
            timeout=settings.model_timeout_seconds,
            max_retries=0,
        ) as client:
            response = client.embeddings.create(
                model=settings.embedding_model, input=texts,
                dimensions=settings.embedding_dimensions, encoding_format="float",
            )
        rows = sorted(response.data, key=lambda row: row.index)
        usage = _embedding_usage(response)
        if diagnostics is not None:
            diagnostics["usage"] = usage
        vectors = [row.embedding for row in rows]
        if len(vectors) != len(texts) or any(
            len(v) != 1024 or not all(math.isfinite(x) for x in v) or not any(v)
            for v in vectors
        ):
            raise ApiError(502, "embedding_invalid", "向量模型返回维度或内容不正确")
        success = True
        return vectors
    except ApiError:
        raise
    except Exception as exc:
        raise provider_error(exc) from None
    finally:
        if diagnostics is not None:
            diagnostics["elapsed_ms"] = (time.monotonic() - started) * 1000
        record_model_call(
            context,
            model_name=settings.embedding_model,
            used_fallback=False,
            elapsed_ms=(time.monotonic() - started) * 1000,
            success=success,
            usage=usage,
        )