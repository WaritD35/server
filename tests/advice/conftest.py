"""In-memory SQLite fixtures for the ``advice`` tests."""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from action.db import Base as ActionBase
from advice import models as advice_models  # noqa: F401 - register tables
from advice.db import Base as AdviceBase
from assessment import models as assessment_models  # noqa: F401 - register tables
from assessment.db import Base as AssessmentBase
from core.db import Base as AuthBase
from knowledge import models as knowledge_models  # noqa: F401 - register tables
from knowledge.db import Base as KnowledgeBase
from registry import models as registry_models  # noqa: F401 - register tables
from registry.db import Base as RegistryBase

_BASES: tuple[type[DeclarativeBase], ...] = (
    AuthBase,
    RegistryBase,
    KnowledgeBase,
    AssessmentBase,
    AdviceBase,
    ActionBase,
)


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(
        schema_translate_map={
            "auth": None,
            "registry": None,
            "knowledge": None,
            "assessment": None,
            "advice": None,
            "action": None,
        }
    )
    for base in _BASES:
        base.metadata.create_all(engine)
    try:
        yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    finally:
        for base in reversed(_BASES):
            base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as s:
        yield s
