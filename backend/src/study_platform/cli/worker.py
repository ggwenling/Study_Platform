"""Run the PostgreSQL task worker until SIGINT or SIGTERM."""

import logging
import signal
from threading import Event

from sqlalchemy.exc import DBAPIError

from study_platform.core.config import get_settings
from study_platform.services.worker_runner import run

logger = logging.getLogger(__name__)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stop = Event()

    def stop_claiming(_signal, _frame):
        stop.set()

    previous = {kind: signal.signal(kind, stop_claiming) for kind in (signal.SIGINT, signal.SIGTERM)}
    try:
        run(stop, get_settings())
    except DBAPIError:
        logger.error("Worker stopped: database configuration or credentials rejected")
        raise SystemExit(1) from None
    except Exception as error:
        logger.error("Worker stopped: %s; check local configuration and storage", type(error).__name__)
        raise SystemExit(1) from None
    finally:
        for kind, handler in previous.items():
            signal.signal(kind, handler)


if __name__ == "__main__":
    main()
