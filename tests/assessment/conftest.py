"""In-memory SQLite wiring, fake frames and a fake VLM probe for the ``assessment`` tests.

``assess_frames`` resolves plants through ``registry`` and species knowledge
through ``knowledge``, so their tables are created alongside the ``assessment``
ones. The frame pair, the frame bytes and ``run_probe`` are swapped on
:mod:`assessment.service`; no MinIO, Langfuse or VLM is touched.
"""

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from assessment_fakes import FakeFrames, FakeProbe
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from assessment import models as assessment_models  # noqa: F401 - register tables
from assessment import service
from assessment.db import Base as AssessmentBase
from core.db import get_session
from core.users.dependencies import get_current_active_user
from core.users.models import User
from knowledge import models as knowledge_models  # noqa: F401 - register tables
from knowledge.db import Base as KnowledgeBase
from knowledge.db import create_views
from knowledge.models import Probe, ProbeSet, ResearchDocument, Species
from registry import models as registry_models  # noqa: F401 - register tables
from registry.db import Base as RegistryBase
from registry.models import Plant

_BASES: tuple[type[DeclarativeBase], ...] = (RegistryBase, KnowledgeBase, AssessmentBase)


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(
        schema_translate_map={"registry": None, "knowledge": None, "assessment": None}
    )
    for base in _BASES:
        base.metadata.create_all(engine)
    # The approved-probe lookup reads staleness from this view.
    with engine.begin() as conn:
        create_views(conn)
    try:
        yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP VIEW IF EXISTS probe_set_freshness"))
        for base in reversed(_BASES):
            base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as s:
        yield s


@pytest.fixture
def add_plant(session_factory: sessionmaker[Session]) -> Callable[..., uuid.UUID]:
    def _add(species_code: str | None = "spath", *, archived: bool = False) -> uuid.UUID:
        plant = Plant(
            id=uuid.uuid4(),
            owner_id=uuid.uuid4(),
            name="Spathi",
            species_code=species_code,
            archived_at=datetime.now(UTC) if archived else None,
        )
        with session_factory() as s:
            s.add(plant)
            s.commit()
        return plant.id

    return _add


@pytest.fixture
def plant_id(add_plant: Callable[..., uuid.UUID]) -> uuid.UUID:
    return add_plant()


@pytest.fixture
def add_species(session_factory: sessionmaker[Session]) -> Callable[..., None]:
    """``add_species(knowledge=...)`` → a ``spath`` catalogue entry, plus an active document."""

    def _add(species_code: str = "spath", *, knowledge: str | None = None) -> None:
        with session_factory() as s:
            s.add(Species(species_code=species_code, scientific_name="Spathiphyllum wallisii"))
            s.flush()
            if knowledge is not None:
                s.add(
                    ResearchDocument(
                        species_code=species_code, title="care", body=knowledge, author="alice"
                    )
                )
            s.commit()

    return _add


@pytest.fixture
def add_probes(session_factory: sessionmaker[Session]) -> Callable[..., uuid.UUID]:
    """``add_probes((slug, priority, is_screening), ...)`` → an approved ``spath`` probe set.

    Registers the species and an active research document too; returns the set's id.
    """

    def _add(*specs: tuple[str, int, bool], species_code: str = "spath") -> uuid.UUID:
        with session_factory() as s:
            if s.get(Species, species_code) is None:
                s.add(Species(species_code=species_code, scientific_name="Spathiphyllum wallisii"))
                s.flush()
            document = ResearchDocument(
                species_code=species_code, title="care", body="Droops when dry.", author="alice"
            )
            s.add(document)
            s.flush()
            probe_set = ProbeSet(
                research_document_id=document.id,
                species_code=species_code,
                source_content_hash=document.content_hash,
                llm_model="test-model",
                status="approved",
            )
            s.add(probe_set)
            s.flush()
            for slug, priority, is_screening in specs:
                care_need = slug.split(".", 1)[0]
                s.add(
                    Probe(
                        probe_set_id=probe_set.id,
                        slug=slug,
                        care_need=care_need,
                        priority=priority,
                        is_screening=is_screening,
                        question=f"Is there {slug}?",
                        worse_looks_like=f"{slug} visible",
                        better_looks_like=f"no {slug}",
                        not_this="a look-alike",
                    )
                )
            s.commit()
            return probe_set.id

    return _add


@pytest.fixture
def frames(monkeypatch: pytest.MonkeyPatch) -> FakeFrames:
    fake = FakeFrames()
    monkeypatch.setattr(service, "get_frame_pair", fake.get_frame_pair)
    monkeypatch.setattr(service, "read_frame", fake.read_frame)
    return fake


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> FakeProbe:
    fake = FakeProbe()
    monkeypatch.setattr(service, "run_probe", fake)
    return fake


def _user(*, is_superuser: bool) -> User:
    return User(
        id=uuid.uuid4(),
        email="ops@example.com" if is_superuser else "alice@example.com",
        name="ops" if is_superuser else "alice",
        hashed_password="x",
        is_active=True,
        is_superuser=is_superuser,
    )


@pytest.fixture
def admin() -> User:
    return _user(is_superuser=True)


@pytest.fixture
def owner() -> User:
    return _user(is_superuser=False)


@pytest.fixture
def app(session_factory: sessionmaker[Session]) -> FastAPI:
    from assessment.api import router as assessment_router

    app = FastAPI()
    app.include_router(assessment_router)

    def _override_session() -> Iterator[Session]:
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _override_session
    return app


@pytest.fixture
def client_as(app: FastAPI) -> Iterator[Callable[[User | None], TestClient]]:
    """``client_as(user)`` → a test client authenticated as ``user`` (``None``: anonymous)."""
    with TestClient(app) as test_client:

        def _as(user: User | None) -> TestClient:
            if user is None:
                app.dependency_overrides.pop(get_current_active_user, None)
            else:
                app.dependency_overrides[get_current_active_user] = lambda: user
            return test_client

        yield _as
