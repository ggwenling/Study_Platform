"""Short synchronous transactions for business APIs and the separate worker."""

import re
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from study_platform.core.config import get_settings

_MISSING_DATABASE = re.compile(
    r'(?:database "[^\r\n]+" does not exist|数据库 "[^\r\n]+" 不存在)\s*$',
    re.IGNORECASE | re.MULTILINE,
)


def connection_failure(error: DBAPIError) -> bool:
    code = getattr(error.orig, "sqlstate", None)
    if code is None and any(message in str(error.orig).lower() for message in (
        "no password supplied", "password authentication failed", "invalid connection option",
        "invalid conninfo", "invalid sslmode",
    )):
        return False
    if code is None and isinstance(error, OperationalError):
        # libpq retains the server bytes when psycopg has no startup SQLSTATE.
        raw = getattr(getattr(error.orig, "pgconn", None), "error_message", b"")
        if _MISSING_DATABASE.search(str(error.orig)) or (
            raw and _MISSING_DATABASE.search(raw.decode("gb18030", errors="replace"))
        ):
            return False
    return (
        error.connection_invalidated
        or (code is None and isinstance(error, OperationalError))
        or (code is not None and code.startswith("08"))
        or code in {"57P01", "57P02", "57P03"}
    )


@lru_cache
def _factory(url: str) -> sessionmaker[Session]:
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True,
                           connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"})
    return sessionmaker(bind=engine, expire_on_commit=False)


def session_factory() -> sessionmaker[Session]:
    return _factory(get_settings().database_url)


def get_db():
    with session_factory()() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
