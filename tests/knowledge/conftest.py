"""In-memory SQLite wiring and seed helpers for the ``knowledge`` tests.

``schema_translate_map`` folds the ``knowledge`` schema into SQLite's default
one; ``PRAGMA foreign_keys=ON`` makes SQLite enforce the FK constraints.
"""

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.db import get_session
from core.users.dependencies import get_current_active_user
from core.users.models import User
from knowledge import models  # noqa: F401 - register tables on Base.metadata
from knowledge.api import router as knowledge_router
from knowledge.db import Base, create_views
from knowledge.models import (
    Probe,
    ProbeAction,
    ProbeExemplar,
    ProbeSet,
    ResearchDocument,
    Species,
)

SeedFn = Callable[..., tuple[ResearchDocument, ProbeSet, Probe]]


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    base_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(base_engine, "connect")
    def _enable_fk(dbapi_conn: Any, _: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    engine = base_engine.execution_options(schema_translate_map={"knowledge": None})
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        create_views(conn)
    try:
        yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    finally:
        Base.metadata.drop_all(engine)
        base_engine.dispose()


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(knowledge_router)

    def _override_session() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = _override_session
    # knowledge endpoints require a superuser; the real get_current_superuser
    # dependency still runs and checks this flag.
    app.dependency_overrides[get_current_active_user] = lambda: User(
        id=uuid.uuid4(),
        email="ops@example.com",
        name="Ops",
        hashed_password="x",
        is_active=True,
        is_superuser=True,
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def add_species(session_factory: sessionmaker[Session]) -> Callable[..., None]:
    """Insert one or more bare species rows."""

    def _add(*codes: str) -> None:
        with session_factory() as session:
            for code in codes:
                if session.get(Species, code) is None:
                    session.add(
                        Species(species_code=code, scientific_name=code.title())
                    )
            session.commit()

    return _add


@pytest.fixture
def seed_full() -> SeedFn:
    """Insert species → document → probe_set → probe → action + exemplar."""

    def _seed(
        session: Session,
        *,
        species_code: str = "spath",
        status: str = "approved",
        body: str = "Keep the soil evenly moist; leaves droop when it dries out.",
        slug: str = "water_deficit.leaf_droop",
    ) -> tuple[ResearchDocument, ProbeSet, Probe]:
        if session.get(Species, species_code) is None:
            session.add(
                Species(species_code=species_code, scientific_name="Spathiphyllum")
            )
            session.flush()
        doc = session.scalars(
            select(ResearchDocument).where(
                ResearchDocument.species_code == species_code,
                ResearchDocument.status == "active",
            )
        ).one_or_none()
        if doc is None or doc.body != body:
            if doc is not None:
                doc.status = "archived"
                session.flush()
            doc = ResearchDocument(
                species_code=species_code, title="notes", body=body, author="alice"
            )
            session.add(doc)
            session.flush()
        probe_set = ProbeSet(
            research_document_id=doc.id,
            species_code=species_code,
            source_content_hash=doc.content_hash,
            llm_model="gpt-4o-2024-11-20",
            status=status,
        )
        session.add(probe_set)
        session.flush()
        probe = Probe(
            probe_set_id=probe_set.id,
            slug=slug,
            care_need="water_deficit",
            priority=1,
            is_screening=True,
            question="Are the leaves drooping?",
            worse_looks_like="limp, folded leaves",
            better_looks_like="firm, upright leaves",
            not_this="natural night-time leaf folding",
        )
        session.add(probe)
        session.flush()
        session.add(
            ProbeAction(
                probe_id=probe.id,
                instruction="Water thoroughly until it drains.",
                urgency="today",
                expect_typical_hours=12,
                expect_max_hours=48,
                expected_signal="leaf_recovery",
            )
        )
        session.add(
            ProbeExemplar(
                probe_id=probe.id,
                role="worse_severe",
                storage_key=f"{species_code}/{slug}/{probe_set.id}.jpg",
                label={"verdict": "worse", "severity": 2},
                origin="own_capture",
            )
        )
        session.commit()
        return doc, probe_set, probe

    return _seed
