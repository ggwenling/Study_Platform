"""Fixed-content probes for saved model connections."""

import time

from langchain_core.messages import HumanMessage

from study_platform.core.errors import ApiError
from study_platform.services.model_connections import selection
from study_platform.services.model_usage import ModelUsageContext, record_model_call


def probe_connection(owner, saved):
    from study_platform.services.model_connections import resolved
    from study_platform.services.models import build_chat_model, embed_texts, provider_error

    token = selection.set({"owner": owner, saved["kind"]: saved})
    try:
        if saved["kind"] == "embedding":
            embed_texts(
                ["连接测试", "connection test"], context=ModelUsageContext(owner, "connection_test")
            )
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
