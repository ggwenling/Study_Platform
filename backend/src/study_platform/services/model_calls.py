"""Chat invocation with optional fallback and usage recording."""

from __future__ import annotations

import logging
import time
from base64 import b64encode
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import replace
from typing import Any

from langchain_core.messages import BaseMessage
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from study_platform.core.config import get_settings
from study_platform.core.errors import ApiError
from study_platform.services.model_connections import resolved
from study_platform.services.model_network import sync_client
from study_platform.services.model_usage import ModelUsageContext, record_model_call
from study_platform.services.models import build_chat_model, provider_error

logger = logging.getLogger(__name__)


def fallback_configured(settings=None) -> bool:
    settings = settings or get_settings()
    return bool(settings.chat_fallback_model.strip())


def should_use_fallback(exc: Exception) -> bool:
    if isinstance(exc, ApiError):
        return exc.code in {"model_timeout", "model_quota_unavailable", "model_service_error"} and (
            exc.retryable or exc.code in {"model_timeout", "model_quota_unavailable"}
        )
    if isinstance(exc, APIStatusError):
        status = exc.status_code
        if status == 429 or status >= 500:
            return True
        return False
    return isinstance(exc, (APIConnectionError, APITimeoutError, TimeoutError))


def _usage_from_response(response: Any) -> dict[str, Any] | None:
    meta = getattr(response, "usage_metadata", None)
    if isinstance(meta, dict) and meta:
        return dict(meta)
    usage = getattr(response, "usage", None)
    if usage is not None and hasattr(usage, "model_dump"):
        return usage.model_dump()
    return None


def _usage_from_chunk(chunk: Any) -> dict[str, Any] | None:
    meta = getattr(chunk, "usage_metadata", None)
    return dict(meta) if isinstance(meta, dict) and meta else None


def invoke_vision(
    prompt_text: str,
    image_bytes: bytes,
    mime_type: str,
    *,
    context: ModelUsageContext,
    saved: dict | None = None,
) -> str:
    settings = get_settings()
    started = time.monotonic()
    model_name = "vision"
    usage = None
    success = False
    client = None
    is_probe = context.purpose == "connection_test"
    timeout = min(20, settings.model_timeout_seconds) if is_probe else settings.model_timeout_seconds
    if saved and saved.get("id"):
        context = replace(
            context,
            connection_source=f"connection:{saved['id']}:{saved.get('revision', 0)}",
        )
    try:
        if not prompt_text.strip():
            raise ApiError(422, "vision_prompt_empty", "视觉理解提示词不能为空。")
        if not image_bytes or mime_type not in ("image/png", "image/jpeg"):
            raise ApiError(422, "vision_image_invalid", "视觉理解只支持有效的 PNG 或 JPEG 图片。")
        connection = resolved(context.owner_user_id, "vision", saved)
        if not connection.get("id"):
            raise ApiError(409, "vision_not_configured", "请先配置并测试视觉理解模型。")
        context = replace(context, connection_source=connection["source"])
        model_name = connection["model"]
        client = OpenAI(
            api_key=connection["api_key"],
            base_url=connection["base_url"],
            http_client=sync_client(connection["base_url"], timeout),
            timeout=timeout,
            max_retries=0,
        )
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{b64encode(image_bytes).decode('ascii')}"
                            },
                        },
                    ],
                }
            ],
            max_tokens=32 if is_probe else settings.model_max_output_tokens,
        )
        usage_obj = getattr(response, "usage", None)
        usage = usage_obj.model_dump() if usage_obj and hasattr(usage_obj, "model_dump") else None
        message = response.choices[0].message
        content = message.content
        if isinstance(content, list):
            content = "\n".join(
                item.text for item in content if isinstance(getattr(item, "text", None), str)
            )
        if not isinstance(content, str) or not content.strip():
            raise ApiError(502, "vision_response_invalid", "视觉模型没有返回可用文本。")
        success = True
        return content.strip()
    except ApiError as error:
        usage = {**(usage or {}), "error_code": error.code}
        raise
    except APIStatusError as error:
        mapped = (
            ApiError(422, "vision_model_unsupported", "视觉模型不支持图像输入，请选择多模态模型。")
            if error.status_code in (400, 404, 422)
            else provider_error(error)
        )
        usage = {**(usage or {}), "error_code": mapped.code}
        raise mapped from None
    except Exception as error:
        mapped = provider_error(error)
        usage = {"error_code": mapped.code}
        raise mapped from None
    finally:
        if client is not None:
            client.close()
        record_model_call(
            context,
            model_name=model_name,
            used_fallback=False,
            elapsed_ms=(time.monotonic() - started) * 1000,
            success=success,
            usage=usage,
        )


def invoke_chat(
    messages: list[BaseMessage],
    *,
    context: ModelUsageContext | None = None,
    timeout: float | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> Any:
    settings = get_settings()
    connection = resolved(context.owner_user_id, "llm") if context else {"source": "platform"}
    personal = bool(connection.get("id"))
    if context:
        context = replace(context, connection_source=connection["source"])
    if personal:
        settings = settings.model_copy(update={"chat_model": connection["model"], "chat_fallback_model": ""})
    started = time.monotonic()
    model_name = settings.chat_model
    used_fallback = False

    def finalize(success: bool, usage: dict[str, Any] | None) -> None:
        record_model_call(
            context,
            model_name=model_name,
            used_fallback=used_fallback,
            elapsed_ms=(time.monotonic() - started) * 1000,
            success=success,
            usage=usage,
        )

    client = None
    try:
        client = build_chat_model(**({"connection": connection} if personal else {}), timeout=timeout, max_tokens=max_tokens, temperature=temperature)
        response = client.invoke(messages)
        finalize(True, _usage_from_response(response))
        return response
    except ApiError:
        finalize(False, None)
        raise
    except Exception as primary_exc:
        if personal or not fallback_configured(settings) or not should_use_fallback(primary_exc):
            finalize(False, None)
            raise provider_error(primary_exc) from None
        used_fallback = True
        model_name = settings.chat_fallback_model
        logger.warning("primary chat model failed; trying fallback", exc_info=primary_exc)
        try:
            response = build_chat_model(
                use_fallback=True, timeout=timeout, max_tokens=max_tokens, temperature=temperature
            ).invoke(messages)
            finalize(True, _usage_from_response(response))
            return response
        except ApiError:
            finalize(False, None)
            raise
        except Exception as fallback_exc:
            finalize(False, None)
            raise provider_error(fallback_exc) from None

    finally:
        if personal and client is not None:
            client.root_client.close()


async def ainvoke_chat(
    messages: list[BaseMessage],
    *,
    context: ModelUsageContext | None = None,
    timeout: float | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> Any:
    settings = get_settings()
    connection = resolved(context.owner_user_id, "llm") if context else {"source": "platform"}
    personal = bool(connection.get("id"))
    if context:
        context = replace(context, connection_source=connection["source"])
    if personal:
        settings = settings.model_copy(update={"chat_model": connection["model"], "chat_fallback_model": ""})
    started = time.monotonic()
    model_name = settings.chat_model
    used_fallback = False

    def finalize(success: bool, usage: dict[str, Any] | None) -> None:
        record_model_call(
            context,
            model_name=model_name,
            used_fallback=used_fallback,
            elapsed_ms=(time.monotonic() - started) * 1000,
            success=success,
            usage=usage,
        )

    client = None
    try:
        client = build_chat_model(
            **({"connection": connection} if personal else {}),
            timeout=timeout, max_tokens=max_tokens, temperature=temperature
        )
        response = await client.ainvoke(messages)
        finalize(True, _usage_from_response(response))
        return response
    except ApiError:
        finalize(False, None)
        raise
    except Exception as primary_exc:
        if personal or not fallback_configured(settings) or not should_use_fallback(primary_exc):
            finalize(False, None)
            raise provider_error(primary_exc) from None
        used_fallback = True
        model_name = settings.chat_fallback_model
        logger.warning("primary chat model failed; trying fallback", exc_info=primary_exc)
        try:
            response = await build_chat_model(
                use_fallback=True, timeout=timeout, max_tokens=max_tokens, temperature=temperature
            ).ainvoke(messages)
            finalize(True, _usage_from_response(response))
            return response
        except ApiError:
            finalize(False, None)
            raise
        except Exception as fallback_exc:
            finalize(False, None)
            raise provider_error(fallback_exc) from None

    finally:
        if personal and client is not None:
            client.root_client.close()
            await client.root_async_client.close()


def chat_model(
    *,
    timeout: float | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
):
    from study_platform.services.models import chat_model as primary_chat_model

    return primary_chat_model(timeout=timeout, max_tokens=max_tokens, temperature=temperature)


async def astream_chat(
    messages: list[BaseMessage],
    *,
    context: ModelUsageContext | None = None,
    timeout: float | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> AsyncIterator[Any]:
    """Stream chat output; switch to fallback only if no chunk was yielded yet."""
    settings = get_settings()
    connection = resolved(context.owner_user_id, "llm") if context else {"source": "platform"}
    personal = bool(connection.get("id"))
    if context:
        context = replace(context, connection_source=connection["source"])
    if personal:
        settings = settings.model_copy(update={"chat_model": connection["model"], "chat_fallback_model": ""})
    started = time.monotonic()
    model_name = settings.chat_model
    used_fallback = False
    usage: dict[str, Any] | None = None
    success = False

    def finalize() -> None:
        record_model_call(
            context,
            model_name=model_name,
            used_fallback=used_fallback,
            elapsed_ms=(time.monotonic() - started) * 1000,
            success=success,
            usage=usage,
        )

    async def pump(use_fb: bool) -> AsyncIterator[Any]:
        nonlocal usage, success
        emitted = False
        client = None
        raw_stream = None
        try:
            client = build_chat_model(
                **({"connection": connection} if personal else {}),
                use_fallback=use_fb, timeout=timeout, max_tokens=max_tokens, temperature=temperature
            )
            raw_stream = client.astream(messages)
            async for chunk in raw_stream:
                emitted = True
                chunk_usage = _usage_from_chunk(chunk)
                if chunk_usage:
                    usage = chunk_usage
                yield chunk
            success = True
        except Exception:
            if emitted:
                success = False
                raise
            raise
        finally:
            if raw_stream is not None and hasattr(raw_stream, "aclose"):
                await raw_stream.aclose()
            if personal and client is not None:
                client.root_client.close()
                await client.root_async_client.close()

    emitted_any = False
    try:
        try:
            async with aclosing(pump(False)) as primary_stream:
                async for chunk in primary_stream:
                    emitted_any = True
                    yield chunk
        except ApiError:
            raise
        except Exception as primary_exc:
            if emitted_any:
                success = False
                raise provider_error(primary_exc) from None
            if personal or not fallback_configured(settings) or not should_use_fallback(primary_exc):
                raise provider_error(primary_exc) from None
            used_fallback = True
            model_name = settings.chat_fallback_model
            logger.warning("primary chat stream failed before output; trying fallback", exc_info=primary_exc)
            async with aclosing(pump(True)) as fallback_stream:
                async for chunk in fallback_stream:
                    emitted_any = True
                    yield chunk
    finally:
        finalize()
