"""Server-side model clients. Provider errors never select another model."""

import math

from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from study_platform.core.config import get_settings
from study_platform.core.errors import ApiError


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


def embed_texts(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    if not settings.embedding_api_key.get_secret_value():
        raise ApiError(503, "model_not_configured", "尚未配置向量模型")
    if not texts or len(texts) > 20:
        raise ValueError("Embedding batch must contain 1 to 20 texts")
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
        vectors = [row.embedding for row in rows]
        if len(vectors) != len(texts) or any(
            len(v) != 1024 or not all(math.isfinite(x) for x in v) or not any(v)
            for v in vectors
        ):
            raise ApiError(502, "embedding_invalid", "向量模型返回维度或内容不正确")
        return vectors
    except ApiError:
        raise
    except Exception as exc:
        raise provider_error(exc) from None


def chat_model() -> ChatOpenAI:
    settings = get_settings()
    if not settings.deepseek_api_key.get_secret_value():
        raise ApiError(503, "model_not_configured", "尚未配置聊天模型")
    return ChatOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.chat_model,
        timeout=settings.model_timeout_seconds,
        max_retries=0,
        max_tokens=settings.model_max_output_tokens,
        extra_body={"thinking": {"type": "disabled"}},
        stream_usage=True,
    )
