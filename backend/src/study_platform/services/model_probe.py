"""Fixed-content probes for saved model connections."""

import time
from base64 import b64decode

import httpx
from langchain_core.messages import HumanMessage

from study_platform.core.config import get_settings
from study_platform.core.errors import ApiError
from study_platform.services.model_connections import selection
from study_platform.services.model_usage import ModelUsageContext, record_model_call

_PROBE_PNG = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAN0lEQVR4nO3RwQ0AMAjDwJT9d05HMB9+vgGCZF7bXJrT9XhgwR8gEyETIRMhEyETIRMhEyEThXzH8QM9OMM6fAAAAABJRU5ErkJggg=="
)


def probe_vision(owner, saved):
    from study_platform.services.model_calls import invoke_vision

    return invoke_vision(
        "Reply with one short word describing this image.",
        _PROBE_PNG,
        "image/png",
        context=ModelUsageContext(owner, "connection_test"),
        saved=saved,
    )


def probe_rerank(owner, saved):
    from study_platform.services.model_connections import resolved
    from study_platform.services.rerank import _post_rerank

    connection = resolved(owner, "rerank", saved)
    if not connection.get("id"):
        raise ApiError(409, "rerank_not_configured", "请先配置并测试 Rerank 连接。")
    started = time.monotonic()
    success = False
    usage = None
    context = ModelUsageContext(owner, "connection_test", connection["source"])
    try:
        results = _post_rerank(
            connection["base_url"],
            connection["api_key"],
            connection["model"],
            "which sentence mentions a test?",
            ["This is a short test document.", "This sentence is unrelated."],
            2,
            min(20, get_settings().rerank_timeout_seconds),
        )
        if not results:
            raise ApiError(502, "model_response_invalid", "Rerank 没有返回有效排序结果。")
        success = True
    except ApiError as error:
        usage = {"error_code": error.code}
        raise
    except httpx.TimeoutException:
        error = ApiError(504, "model_timeout", "模型服务连接超时", retryable=True)
        usage = {"error_code": error.code}
        raise error from None
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        code = "model_auth_failed" if status in (401, 403) else "model_quota_unavailable" if status in (402, 429) else "model_service_error"
        mapped = ApiError(502, code, "Rerank 服务请求失败。", retryable=status >= 500)
        usage = {"error_code": mapped.code}
        raise mapped from None
    except Exception as error:
        from study_platform.services.models import provider_error

        mapped = provider_error(error)
        usage = {"error_code": mapped.code}
        raise mapped from None
    finally:
        record_model_call(
            context,
            model_name=connection["model"],
            used_fallback=False,
            elapsed_ms=(time.monotonic() - started) * 1000,
            success=success,
            usage=usage,
        )


def probe_connection(owner, saved):
    from study_platform.services.model_connections import resolved
    from study_platform.services.models import build_chat_model, embed_texts, provider_error

    token = selection.set({"owner": owner, saved["kind"]: saved})
    try:
        if saved["kind"] == "embedding":
            embed_texts(
                ["连接测试", "connection test"], context=ModelUsageContext(owner, "connection_test")
            )
        elif saved["kind"] == "vision":
            probe_vision(owner, saved)
        elif saved["kind"] == "rerank":
            probe_rerank(owner, saved)
        else:
            connection = resolved(owner, "llm", saved)
            client = build_chat_model(connection=connection, max_tokens=16, timeout=20)
            started = time.monotonic()
            success = False
            usage = None
            try:
                stream = client.stream([HumanMessage(content="Reply OK.")])
                found = False
                for chunk in stream:
                    found = found or isinstance(chunk.content, str) and bool(chunk.content.strip())
                    usage = chunk.usage_metadata or usage
                if not found:
                    raise ApiError(502, "model_response_invalid", "模型没有返回流式文本。")
                success = True
            finally:
                client.root_client.close()
                record_model_call(
                    ModelUsageContext(owner, "connection_test", connection["source"]),
                    model_name=connection["model"],
                    used_fallback=False,
                    elapsed_ms=(time.monotonic() - started) * 1000,
                    success=success,
                    usage=usage,
                )
                # The asynchronous client is unused by this synchronous probe.
    except ApiError:
        raise
    except Exception as error:
        raise provider_error(error) from None
    finally:
        selection.reset(token)
