"""Short synchronous transactions for business APIs and the separate worker."""

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from study_platform.core.config import get_settings


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
