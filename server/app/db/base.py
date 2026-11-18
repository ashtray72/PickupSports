"""
SQLAlchemy declarative base and model registry.

Import `server.app.models` (not this module) when you need the complete
`Base.metadata`, e.g. for Alembic autogenerate / `alembic check`.
"""

from __future__ import annotations

import enum

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase

# Deterministic constraint names so migrations and `alembic check` stay stable.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


# Keyword arguments shared by every UUID primary key column.
UUID_PK = dict(primary_key=True, server_default=sa.text("gen_random_uuid()"))


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)


def pg_enum(enum_cls: type[enum.Enum], name: str) -> sa.Enum:
    """PostgreSQL ENUM type that stores the Python enum's *values*."""
    return sa.Enum(
        enum_cls,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )
