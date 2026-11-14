"""Run once as a deployment step after the study_graph schema is created."""

import asyncio
import sys

from study_platform.ai.checkpointer import open_checkpointer


async def _setup() -> None:
    async with open_checkpointer() as checkpointer:
        await checkpointer.setup()


def main() -> None:
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(_setup())


if __name__ == "__main__":
    main()
