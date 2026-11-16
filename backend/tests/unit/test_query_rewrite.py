import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from study_platform.core.config import Settings
from study_platform.services import chat, models, query_rewrite


@pytest.mark.parametrize(
    "content",
    [
        '{"query":"QZ-732柜在哪里？"}',
        '{"query":"“雪松”项目中的QZ-731和B-99柜在哪里？"}',
        '{"query":"“雪松”项目的qz-731柜位置在哪里？"}',
        '{"query":"雪松项目有几个柜子？"}',
        '{"query":""}',
        '{"query":42}',
        '{"answer":"731"}',
        "not JSON",
    ],
)
def test_invalid_or_changed_identifiers_fall_back(content):
    assert query_rewrite.validated_query("“雪松”项目的QZ-731柜在哪里？", content) is None


def test_valid_rewrite_preserves_numbers_and_quoted_entities():
    assert (
        query_rewrite.validated_query(
            "“雪松”项目的QZ-731柜在哪里？",
            '{"query":"“雪松”项目中QZ-731储物柜的所在位置？"}',
        )
        == "“雪松”项目中QZ-731储物柜的所在位置？"
    )
    assert query_rewrite.validated_query("原问题？", '{"query":"原问题？"}') is None


def test_rewrite_calls_once_with_bounded_client_and_only_current_question(monkeypatch):
    invoke = AsyncMock(
        return_value=SimpleNamespace(
            content='{"query":"雪松项目仓库允许的最高温度是多少？"}',
            usage_metadata={"total_tokens": 32},
        )
    )
    monkeypatch.setattr(query_rewrite, "ainvoke_chat", invoke)
    result = asyncio.run(query_rewrite.rewrite_query("雪松库房最多能热到什么程度？"))
    invoke.assert_awaited_once()
    assert invoke.await_args.kwargs["timeout"] == 8
    assert invoke.await_args.kwargs["max_tokens"] == 256
    assert result.query and result.reason == "rewritten"
    assert result.usage == {"total_tokens": 32}
    messages = invoke.await_args.args[0]
    assert len(messages) == 2
    assert messages[-1].content == "雪松库房最多能热到什么程度？"


def test_timeout_falls_back_without_retry_and_cancellation_is_not_swallowed(monkeypatch):
    calls: list[int] = []

    async def tracked_slow(*_args, **_kwargs):
        calls.append(1)
        await asyncio.sleep(60)

    monkeypatch.setattr(query_rewrite, "REWRITE_TIMEOUT", 0.01)
    monkeypatch.setattr(query_rewrite, "ainvoke_chat", tracked_slow)
    result = asyncio.run(query_rewrite.rewrite_query("问题"))
    assert result.query is None and result.reason == "timeout"
    assert len(calls) == 1
    invoke = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(query_rewrite, "ainvoke_chat", invoke)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(query_rewrite.rewrite_query("问题"))


def test_provider_and_parse_errors_fall_back(monkeypatch):
    invoke = AsyncMock(side_effect=RuntimeError("private details"))
    monkeypatch.setattr(query_rewrite, "ainvoke_chat", invoke)
    assert asyncio.run(query_rewrite.rewrite_query("问题")).reason == "unavailable"
    invoke.side_effect = None
    invoke.return_value = SimpleNamespace(content="broken JSON", usage_metadata=None)
    assert asyncio.run(query_rewrite.rewrite_query("问题")).query is None


def test_rrf_deduplicates_limits_and_breaks_ties_by_original_rank():
    def row(key):
        return {"id": key}

    assert chat.fuse_rankings(
        [[row("a"), row("b"), row("c")], [row("b"), row("d"), row("a")]], 3
    ) == [row("b"), row("a"), row("d")]
    assert chat.fuse_rankings([[row("a"), row("b")], [row("x"), row("y")]], 4) == [
        row("a"),
        row("x"),
        row("b"),
        row("y"),
    ]


def test_retrieval_batches_both_queries_and_keeps_scope(monkeypatch):
    embed = Mock(return_value=[[1], [2]])
    retrieve = Mock(return_value=[])
    monkeypatch.setattr(chat, "embed_texts", embed)
    monkeypatch.setattr(chat, "retrieve_hybrid", retrieve)
    from uuid import uuid4

    owner = uuid4()
    scope = {"index_version_ids": ["fixed"]}
    chat.retrieve(scope, "原问题", owner, rewritten_query="同义问题")
    embed.assert_called_once()
    assert embed.call_args.args[0] == ["原问题", "同义问题"]
    assert embed.call_args.kwargs["context"].purpose == "embedding"
    retrieve.assert_called_once_with(scope, ["原问题", "同义问题"], [[1], [2]], owner)
    assert scope == {"index_version_ids": ["fixed"]}
    embed.reset_mock()
    chat.retrieve({"index_version_ids": []}, "原问题", owner, rewritten_query="同义问题")
    embed.assert_not_called()


def test_rewrite_client_does_not_change_answer_client_defaults(monkeypatch):
    settings = Settings(_env_file=None, deepseek_api_key="test")
    constructor = Mock()
    monkeypatch.setattr(models, "get_settings", lambda: settings)
    monkeypatch.setattr(models, "ChatOpenAI", constructor)
    models.chat_model(timeout=8, max_tokens=256, temperature=0)
    assert constructor.call_args.kwargs["max_retries"] == 0
    assert constructor.call_args.kwargs["timeout"] == 8
    models.chat_model()
    assert constructor.call_args.kwargs["timeout"] == settings.model_timeout_seconds
    assert constructor.call_args.kwargs["max_tokens"] == settings.model_max_output_tokens
