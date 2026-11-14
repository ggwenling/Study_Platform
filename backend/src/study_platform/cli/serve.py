import sys

import uvicorn


def main() -> None:
    # Psycopg async connections require SelectorEventLoop on Windows.
    uvicorn.run(
        "study_platform.main:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
        loop="asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto",
    )


if __name__ == "__main__":
    main()
