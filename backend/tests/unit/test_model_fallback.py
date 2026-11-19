"""Deterministic tests for chat model fallback eligibility and switching."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langchain_core.messages import HumanMessage
from openai import APIStatusError

from study_platform.core.config import Settings
from study_platform.core.errors import ApiError
from study_platform.services import model_calls, models


def _status_error(status: int) -> APIStatusError:
    response = Mock()
    response.status_code = status
    return APIStatusError("failure", response=response, body=None)


@pytest.mark.parametrize(
    "exc,expected",
    [
        (_status_error(429), True),
        (_status_error(503), True),
        (_status_error(400), False),
        (_status_error(422), False),
        (_status_error(401), False),
        (TimeoutError(), True),
        (ApiError(504, "model_timeout", "timeout", retryable=True), True),
        (ApiError(503, "model_quota_unavailable", "quota"), True),
        (ApiError(502, "model_auth_failed", "auth"), False),
    ],
)
def test_should_use_fallback(exc, expected):
    assert model_calls.should_use_fallback(exc) is expected


def test_invoke_chat_uses_fallback_only_when_configured(monkeypatch):
    settings = Settings(
        _env_file=None,
        deepseek_api_key="primary",
        chat_model="primary-model",
        chat_fallback_model="backup-model",
    )
    primary = Mock(side_effect=_status_error(503))
    backup = Mock(return_value=SimpleNamespace(content="ok", usage_metadata={"total_tokens": 3}))
    calls: list[bool] = []

    def factory(*, use_fallback=False, **kwargs):
        calls.append(use_fallback)
        return Mock(invoke=primary if not use_fallback else backup)

    monkeypatch.setattr(model_calls, "get_settings", lambda: settings)
    monkeypatch.setattr(model_calls, "build_chat_model", factory)
    monkeypatch.setattr(model_calls, "record_model_call", lambda *args, **kwargs: None)

    response = model_calls.invoke_chat([HumanMessage(content="hello")])
    assert response.content == "ok"
    assert calls == [False, True]
    primary.assert_called_once()
    backup.assert_called_once()


def test_invoke_chat_does_not_fallback_on_client_error(monkeypatch):
    settings = Settings(_env_file=None, deepseek_api_key="primary", chat_fallback_model="backup-model")
    primary = Mock(side_effect=_status_error(400))

    def factory(*, use_fallback=False, **kwargs):
        return Mock(invoke=primary)

    monkeypatch.setattr(model_calls, "get_settings", lambda: settings)
    monkeypatch.setattr(model_calls, "build_chat_model", factory)
    monkeypatch.setattr(model_calls, "record_model_call", lambda *args, **kwargs: None)

    with pytest.raises(ApiError) as error:
        model_calls.invoke_chat([HumanMessage(content="hello")])
    assert error.value.code != "model_not_configured"
    primary.assert_called_once()


def test_invoke_chat_without_fallback_configured_preserves_prior_behavior(monkeypatch):
    settings = Settings(_env_file=None, deepseek_api_key="primary", chat_fallback_model="")
    primary = Mock(side_effect=_status_error(503))

    def factory(*, use_fallback=False, **kwargs):
        assert use_fallback is False
        return Mock(invoke=primary)

    monkeypatch.setattr(model_calls, "get_settings", lambda: settings)
    monkeypatch.setattr(model_calls, "build_chat_model", factory)
    monkeypatch.setattr(model_calls, "record_model_call", lambda *args, **kwargs: None)

    with pytest.raises(ApiError):
        model_calls.invoke_chat([HumanMessage(content="hello")])
    primary.assert_called_once()


async def _collect_stream(gen):
    return [chunk async for chunk in gen]


def test_astream_chat_fallback_only_before_first_delta(monkeypatch):
    settings = Settings(
        _env_file=None,
        deepseek_api_key="primary",
        chat_model="primary-model",
        chat_fallback_model="backup-model",
    )
    primary_chunks = [SimpleNamespace(content="partial", usage_metadata=None)]

    async def primary_stream(_):
        yield primary_chunks[0]
        raise _status_error(503)

    async def backup_stream(_):
        yield SimpleNamespace(content="rest", usage_metadata={"total_tokens": 2})

    def factory(*, use_fallback=False, **kwargs):
        stream = backup_stream if use_fallback else primary_stream
        return Mock(astream=stream)

    monkeypatch.setattr(model_calls, "get_settings", lambda: settings)
    monkeypatch.setattr(model_calls, "build_chat_model", factory)
    monkeypatch.setattr(model_calls, "record_model_call", lambda *args, **kwargs: None)

    with pytest.raises(ApiError):
        import asyncio

        asyncio.run(_collect_stream(model_calls.astream_chat([HumanMessage(content="x")])))


def test_astream_chat_switches_when_primary_fails_before_output(monkeypatch):
    settings = Settings(
        _env_file=None,
        deepseek_api_key="primary",
        chat_fallback_model="backup-model",
    )

    async def primary_stream(_):
        raise _status_error(429)
        yield  # pragma: no cover

    async def backup_stream(_):
        yield SimpleNamespace(content="ok", usage_metadata={"total_tokens": 1})

    def factory(*, use_fallback=False, **kwargs):
        return Mock(astream=backup_stream if use_fallback else primary_stream)

    monkeypatch.setattr(model_calls, "get_settings", lambda: settings)
    monkeypatch.setattr(model_calls, "build_chat_model", factory)
    monkeypatch.setattr(model_calls, "record_model_call", lambda *args, **kwargs: None)

    import asyncio

    chunks = asyncio.run(_collect_stream(model_calls.astream_chat([HumanMessage(content="x")])))
    assert len(chunks) == 1
    assert chunks[0].content == "ok"


def test_provider_error_marks_five_xx_retryable():
    error = models.provider_error(_status_error(502))
    assert error.retryable is True


def test_astream_chat_aclose_finalizes_usage_and_closes_personal_client(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from study_platform.services.model_usage import ModelUsageContext

    settings = Settings(_env_file=None, deepseek_api_key="primary", chat_model="platform-model")
    raw_closed = False
    recorded: list[dict] = []

    async def raw_stream(_):
        nonlocal raw_closed
        try:
            yield SimpleNamespace(content="first", usage_metadata={"total_tokens": 5})
            yield SimpleNamespace(content="second", usage_metadata={"total_tokens": 9})
        finally:
            raw_closed = True

    root_client = Mock()
    root_async_client = Mock(close=AsyncMock())
    client = Mock(
        astream=raw_stream,
        root_client=root_client,
        root_async_client=root_async_client,
    )

    monkeypatch.setattr(model_calls, "get_settings", lambda: settings)
    monkeypatch.setattr(
        model_calls,
        "resolved",
        lambda owner, kind: {"id": "personal-1", "source": "personal", "model": "custom-model"},
    )
    monkeypatch.setattr(model_calls, "build_chat_model", lambda **kwargs: client)
    monkeypatch.setattr(
        model_calls,
        "record_model_call",
        lambda ctx, **kwargs: recorded.append({"ctx": ctx, **kwargs}),
    )

    async def run():
        stream = model_calls.astream_chat(
            [HumanMessage(content="x")],
            context=ModelUsageContext(uuid4(), "chat"),
        )
        first = await anext(stream)
        assert first.content == "first"
        assert raw_closed is False
        assert recorded == []
        await stream.aclose()
        assert raw_closed is True
        root_client.close.assert_called_once()
        root_async_client.close.assert_awaited_once()
        assert len(recorded) == 1
        assert recorded[0]["model_name"] == "custom-model"
        assert recorded[0]["success"] is False
        assert recorded[0]["usage"] == {"total_tokens": 5}

    asyncio.run(run())
