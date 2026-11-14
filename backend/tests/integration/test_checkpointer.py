"""Requires a migrated database and the separately initialized checkpointer."""

import asyncio
import os
import sys
from typing import TypedDict
from uuid import uuid4

import pytest
from langgraph.graph import END, START, StateGraph
from sqlalchemy.engine import make_url

from study_platform.ai.checkpointer import open_checkpointer
from study_platform.core.config import get_settings


class State(TypedDict):
    value: str


@pytest.mark.integration
def test_checkpoint_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.getenv("RUN_DB_TESTS") != "1":
        pytest.skip("set RUN_DB_TESTS=1 to run PostgreSQL tests")
    test_url = os.getenv("TEST_GRAPH_DATABASE_URL")
    if not test_url:
        pytest.skip("TEST_GRAPH_DATABASE_URL is not configured")
    database = make_url(test_url).database
    if not database or not database.endswith("_test"):
        pytest.fail("TEST_GRAPH_DATABASE_URL database name must end in _test")

    async def run() -> None:
        config = {"configurable": {"thread_id": f"test:{uuid4()}"}}
        builder = StateGraph(State)
        builder.add_node("record", lambda state: {"value": state["value"] + " saved"})
        builder.add_edge(START, "record")
        builder.add_edge("record", END)

        async with open_checkpointer() as checkpointer:
            graph = builder.compile(checkpointer=checkpointer)
            assert await graph.ainvoke({"value": "checkpoint"}, config) == {
                "value": "checkpoint saved"
            }

        async with open_checkpointer() as checkpointer:
            try:
                graph = builder.compile(checkpointer=checkpointer)
                state = await graph.aget_state(config)
                assert state.values == {"value": "checkpoint saved"}
            finally:
                await checkpointer.adelete_thread(config["configurable"]["thread_id"])

    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        with monkeypatch.context() as patch:
            patch.setenv("GRAPH_DATABASE_URL", test_url)
            get_settings.cache_clear()
            with asyncio.Runner(loop_factory=loop_factory) as runner:
                runner.run(run())
    finally:
        get_settings.cache_clear()
