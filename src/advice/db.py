"""SQLAlchemy wiring for the ``advice`` schema."""

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase

from core.db import engine as _core_engine

ADVICE_SCHEMA = "advice"

if _core_engine.dialect.name == "postgresql":
    engine = _core_engine
else:
    # SQLite has no schemas: fold ``advice`` into default schema for tests
    _existing = _core_engine.get_execution_options().get("schema_translate_map", {})
    engine = _core_engine.execution_options(
        schema_translate_map={**_existing, ADVICE_SCHEMA: None}
    )


def utcnow() -> datetime:
    """Timezone-aware 'now' in UTC, used as column default."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for tables owned by the ``advice`` context."""


def init_models() -> None:
    """Create the ``advice`` schema and its tables."""
    from advice import models  # noqa: F401  -- register models on Base.metadata

    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{ADVICE_SCHEMA}"'))
        Base.metadata.create_all(conn)
