"""Connections to the separately managed LangGraph checkpoint schema."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from study_platform.core.config import get_settings


@asynccontextmanager
async def open_checkpointer() -> AsyncIterator[AsyncPostgresSaver]:
    async with await AsyncConnection.connect(
        get_settings().graph_database_url,
        connect_timeout=5,
        autocommit=True,
        row_factory=dict_row,
        prepare_threshold=0,
        options="-c search_path=study_graph",
    ) as connection:
        yield AsyncPostgresSaver(connection)
