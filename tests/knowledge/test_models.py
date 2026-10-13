"""Sanity checks for the ``knowledge`` schema models."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from knowledge.db import Base
from knowledge.hashing import content_hash
from knowledge.models import (
    Probe,
    ProbeAction,
    ProbeExemplar,
    ProbeSet,
    ResearchDocument,
    Species,
)


def _seed_probe_set(
    session: Session, *, body: str = "Keep the soil moist.", status: str = "approved"
) -> ProbeSet:
    """species -> document -> probe_set, inserted parent-first."""
    if session.get(Species, "spath") is None:
        session.add(
            Species(species_code="spath", scientific_name="Spathiphyllum wallisii")
        )
        session.flush()
    doc = ResearchDocument(
        species_code="spath",
        title="Spathiphyllum — watering & light",
        body=body,
        author="alice",
    )
    session.add(doc)
    session.flush()
    probe_set = ProbeSet(
        research_document_id=doc.id,
        species_code="spath",
        source_content_hash=doc.content_hash,
        llm_model="gpt-4o-2024-11-20",
        status=status,
    )
    session.add(probe_set)
    session.flush()
    return probe_set


def test_metadata_covers_every_table() -> None:
    assert {t.name for t in Base.metadata.sorted_tables} == {
        "species",
        "research_document",
        "probe_set",
        "probe",
        "probe_action",
        "probe_exemplar",
        "knowledge_chunks",
    }


def test_generation_round_trips(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        probe_set = _seed_probe_set(session)
        doc = session.get(ResearchDocument, probe_set.research_document_id)
        assert doc is not None
        assert doc.content_hash == content_hash(doc.body)  # filled in automatically
        assert doc.status == "active"  # default

        probe = Probe(
            probe_set_id=probe_set.id,
            slug="water_deficit.leaf_droop",
            care_need="water_deficit",
            priority=1,
            is_screening=True,
            question="Are the leaves drooping?",
            worse_looks_like="limp, folded leaves",
            better_looks_like="firm, upright leaves",
            not_this="natural night-time leaf folding",
            evidence_quote="the leaves droop when it dries out",
        )
        session.add(probe)
        session.flush()

        session.add(
            ProbeAction(
                probe_id=probe.id,
                instruction="Water thoroughly until it drains from the pot.",
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
                storage_key="spath/water_deficit.leaf_droop/worse_severe/001.jpg",
                label={"verdict": "worse", "severity": 2},
                origin="own_capture",
            )
        )
        session.commit()

    with session_factory() as session:
        loaded = session.scalars(select(Probe)).one()
        assert loaded.is_screening is True
        assert loaded.evidence_quote == "the leaves droop when it dries out"

        action = session.scalars(select(ProbeAction)).one()
        assert action.expect_max_hours == 48
        assert action.ordering == 0  # default

        exemplar = session.scalars(select(ProbeExemplar)).one()
        assert exemplar.label == {"verdict": "worse", "severity": 2}
        assert exemplar.license is None


def test_freshness_view_flags_outdated_research(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add(Species(species_code="spath", scientific_name="Spathiphyllum"))
        session.flush()
        v1 = ResearchDocument(
            species_code="spath",
            title="notes",
            body="v1 text",
            author="alice",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session.add(v1)
        session.flush()
        probe_set = ProbeSet(
            research_document_id=v1.id,
            species_code="spath",
            source_content_hash=v1.content_hash,
            llm_model="m",
            status="approved",
        )
        session.add(probe_set)
        session.commit()

        stale = session.execute(
            text("SELECT is_stale FROM probe_set_freshness")
        ).scalar_one()
        assert not stale

        # A revision is a new row; the one it supersedes is archived, not edited.
        v1.status = "archived"
        session.flush()
        session.add(
            ResearchDocument(
                species_code="spath",
                title="notes",
                body="v2 text — revised",
                author="alice",
                created_at=datetime(2026, 6, 1, tzinfo=UTC),
            )
        )
        session.commit()

        stale = session.execute(
            text("SELECT is_stale FROM probe_set_freshness")
        ).scalar_one()
        assert stale


def test_one_active_document_per_species(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add(Species(species_code="spath", scientific_name="Spathiphyllum"))
        session.flush()
        common = dict(species_code="spath", title="notes", author="alice")
        session.add(ResearchDocument(body="v1", **common))
        session.add(ResearchDocument(body="v2", **common))
        with pytest.raises(IntegrityError):
            session.commit()


def test_one_approved_probe_set_per_species(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        first = _seed_probe_set(session)
        second = ProbeSet(
            research_document_id=first.research_document_id,
            species_code="spath",
            source_content_hash=first.source_content_hash,
            llm_model="m",
            status="approved",
        )
        session.add(second)
        with pytest.raises(IntegrityError):
            session.commit()


def test_drafts_and_archived_sets_are_unconstrained(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        first = _seed_probe_set(session, status="draft")
        for status in ("draft", "archived", "archived"):
            session.add(
                ProbeSet(
                    research_document_id=first.research_document_id,
                    species_code="spath",
                    source_content_hash=first.source_content_hash,
                    llm_model="m",
                    status=status,
                )
            )
        session.commit()  # no unique index applies outside 'approved'
        assert len(session.scalars(select(ProbeSet)).all()) == 4


def test_probe_set_species_must_match_its_document(
    session_factory: sessionmaker[Session],
) -> None:
    """The composite FK stops the denormalised ``species_code`` from drifting."""
    with session_factory() as session:
        probe_set = _seed_probe_set(session)
        session.add(Species(species_code="ficus", scientific_name="Ficus"))
        session.flush()
        session.add(
            ProbeSet(
                research_document_id=probe_set.research_document_id,  # a 'spath' doc
                species_code="ficus",
                source_content_hash="x",
                llm_model="m",
                status="draft",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_probe_requires_its_set(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session, pytest.raises(IntegrityError):
        session.add(
            Probe(
                probe_set_id=uuid.uuid4(),  # no such set
                slug="x",
                care_need="water_deficit",
                priority=1,
                question="?",
                worse_looks_like="a",
                better_looks_like="b",
                not_this="c",
            )
        )
        session.commit()


def test_probe_slug_unique_within_set(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        probe_set = _seed_probe_set(session)
        common = dict(
            probe_set_id=probe_set.id,
            care_need="water_deficit",
            priority=1,
            question="?",
            worse_looks_like="a",
            better_looks_like="b",
            not_this="c",
        )
        session.add(Probe(slug="dup", **common))
        session.add(Probe(slug="dup", **common))
        with pytest.raises(IntegrityError):
            session.commit()
