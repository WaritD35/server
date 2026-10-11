"""SQLAlchemy engine / session lifecycle and the declarative base.

``core`` owns the ``auth`` schema; every model that lives there inherits from
:class:`Base` and sets ``{"schema": AUTH_SCHEMA}`` in ``__table_args__``.

Until per-schema Alembic migrations land (see ``core`` README), :func:`init_models`
creates the schema and its tables directly — enough for local runs and tests.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from core.config import get_settings

AUTH_SCHEMA = "auth"


class Base(DeclarativeBase):
    """Declarative base for tables owned by ``core`` (the ``auth`` schema)."""


engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)

if engine.dialect.name != "postgresql":
    # SQLite (local quick-checks / tests) has no schemas: fold ``auth`` into the
    # default schema so the schema-qualified models run unchanged.
    engine = engine.execution_options(schema_translate_map={AUTH_SCHEMA: None})

session_factory = SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yield a session and always close it."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_models() -> None:
    """Create the ``auth`` schema and its tables. Interim helper until Alembic."""
    import core.devices.models  # noqa: F401  -- register models on Base.metadata
    import core.users.models  # noqa: F401

    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{AUTH_SCHEMA}"'))
        Base.metadata.create_all(conn)
