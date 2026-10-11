"""SQLAlchemy wiring for the ``companion`` schema."""

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase

from core.db import engine as _core_engine

COMPANION_SCHEMA = "companion"

if _core_engine.dialect.name == "postgresql":
    engine = _core_engine
else:
    _existing = _core_engine.get_execution_options().get("schema_translate_map", {})
    engine = _core_engine.execution_options(
        schema_translate_map={**_existing, COMPANION_SCHEMA: None}
    )


def utcnow() -> datetime:
    """Timezone-aware 'now' in UTC."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for tables owned by the ``companion`` context."""


def init_models() -> None:
    """Create the ``companion`` schema and its tables."""
    from companion import models  # noqa: F401  -- register models on Base.metadata

    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{COMPANION_SCHEMA}"'))
        Base.metadata.create_all(conn)
