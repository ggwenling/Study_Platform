"""Shared SQLAlchemy 2 declarative metadata for the study schema."""

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    metadata = sa.MetaData(
        naming_convention={
            "pk": "pk_%(table_name)s",
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s",
        }
    )
