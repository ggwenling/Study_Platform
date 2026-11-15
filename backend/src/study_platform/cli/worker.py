"""Run the PostgreSQL ingestion worker."""

import logging
import time

from sqlalchemy.exc import DBAPIError

from study_platform.core.config import get_settings
from study_platform.persistence.database import connection_failure
from study_platform.services.ingestion import run_once

logger = logging.getLogger(__name__)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    delay = 1
    while True:
        try:
            worked = run_once()
        except DBAPIError as error:
            if not connection_failure(error):
                logger.error("Worker stopped: database configuration or credentials rejected")
                raise SystemExit(1) from None
            # Do not log the exception: SQL parameters can contain private document text.
            logger.warning("Database unavailable; retrying in %s seconds", delay)
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        except Exception as error:
            logger.error(
                "Worker stopped: %s; check local configuration and storage", type(error).__name__
            )
            raise SystemExit(1) from None
        if delay > 1:
            logger.info("Database connection recovered")
        delay = 1
        if not worked:
            time.sleep(get_settings().worker_poll_seconds)


if __name__ == "__main__":
    main()
