import asyncio
import sys

import uvicorn


def main() -> None:
    # Psycopg async connections require SelectorEventLoop on Windows.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    uvicorn.run("study_platform.main:create_app", factory=True, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
