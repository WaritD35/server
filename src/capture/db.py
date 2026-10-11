"""SQLAlchemy wiring for the ``vision`` schema.

The ``capture`` context owns the ``vision`` Postgres schema. Every model inherits
from :class:`Base` and sets ``{"schema": VISION_SCHEMA}`` in ``__table_args__``.
"""

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase

from core.db import engine as _core_engine

VISION_SCHEMA = "vision"

if _core_engine.dialect.name == "postgresql":
    engine = _core_engine
else:
    # SQLite has no schemas: fold ``vision`` into the default one.
    _existing = _core_engine.get_execution_options().get("schema_translate_map", {})
    engine = _core_engine.execution_options(
        schema_translate_map={**_existing, VISION_SCHEMA: None}
    )


def utcnow() -> datetime:
    """Timezone-aware 'now' in UTC, used as column default."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for tables owned by the ``capture`` context."""


def init_models() -> None:
    """Create the ``vision`` schema and its tables."""
    from capture import models  # noqa: F401  -- register models on Base.metadata

    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{VISION_SCHEMA}"'))
        Base.metadata.create_all(conn)
