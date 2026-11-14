"""Run the PostgreSQL ingestion worker."""

import time

from study_platform.core.config import get_settings
from study_platform.services.ingestion import run_once


def main():
    while True:
        if not run_once():
            time.sleep(get_settings().worker_poll_seconds)


if __name__ == "__main__":
    main()
