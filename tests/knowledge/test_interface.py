"""Published in-process interface: ``get_species_probes`` and ``get_approved_checks``."""

import json
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from knowledge.interface import get_approved_checks, get_species_probes
from knowledge.models import Probe, ProbeAction, ProbeSet

SeedFn = Callable[..., Any]


def test_returns_none_without_an_approved_set(
    session_factory: sessionmaker[Session], seed_full: SeedFn
) -> None:
    with session_factory() as session:
        seed_full(session, status="draft")
        assert get_species_probes(session, "spath") is None


def test_returns_json_serialisable_bundle(
    session_factory: sessionmaker[Session], seed_full: SeedFn
) -> None:
    with session_factory() as session:
        seed_full(session)
        bundle = get_species_probes(session, "spath")

    assert bundle is not None
    assert bundle.species_code == "spath"
    assert bundle.is_stale is False

    payload = bundle.model_dump(mode="json")
    assert json.loads(json.dumps(payload))  # round-trips as plain JSON
    probe = payload["probes"][0]
    assert probe["care_need"] == "water_deficit"
    assert probe["actions"][0]["expect_max_hours"] == 48
    assert probe["exemplars"][0]["label"] == {"verdict": "worse", "severity": 2}


# --------------------------------------------------------------------------- #
# get_approved_checks: only the structured knowledge a caller needs
# --------------------------------------------------------------------------- #


def _add_check(
    session: Session, probe_set: ProbeSet, slug: str, priority: int, *, screening: bool = False
) -> None:
    probe = Probe(
        probe_set_id=probe_set.id,
        slug=slug,
        care_need=slug.split(".")[0],
        priority=priority,
        is_screening=screening,
        question=f"Is there {slug}?",
        worse_looks_like="worse",
        better_looks_like="better",
        not_this="a look-alike",
        evidence_quote=f"Quote for {slug}.",
    )
    session.add(probe)
    session.flush()
    for ordering in (2, 0, 1):
        session.add(
            ProbeAction(
                probe_id=probe.id,
                ordering=ordering,
                instruction=f"{slug} step {ordering}",
                urgency="this_week",
                expect_typical_hours=24,
                expect_max_hours=72,
            )
        )
    session.flush()


def _seed_checks(session: Session, seed_full: SeedFn) -> None:
    """Approved set: leaf_droop (screening, p1), pest.spots (p3), light.bleach (p2)."""
    _doc, probe_set, _probe = seed_full(session)
    _add_check(session, probe_set, "pest.spots", 3)
    _add_check(session, probe_set, "light.bleach", 2)
    session.commit()


def test_approved_checks_are_screening_first_then_by_priority(
    session_factory: sessionmaker[Session], seed_full: SeedFn
) -> None:
    with session_factory() as session:
        _seed_checks(session, seed_full)
        bundle = get_approved_checks(session, "spath")

    assert bundle is not None
    assert [p.slug for p in bundle.probes] == [
        "water_deficit.leaf_droop",
        "light.bleach",
        "pest.spots",
    ]


def test_only_the_named_checks_come_back_in_the_order_given(
    session_factory: sessionmaker[Session], seed_full: SeedFn
) -> None:
    with session_factory() as session:
        _seed_checks(session, seed_full)
        bundle = get_approved_checks(
            session, "spath", ["pest.spots", "no.longer_approved", "light.bleach", "pest.spots"]
        )

    assert bundle is not None
    assert [p.slug for p in bundle.probes] == ["pest.spots", "light.bleach"]
    spots = bundle.probes[0]
    assert spots.evidence_quote == "Quote for pest.spots."
    assert [a.instruction for a in spots.actions] == [
        "pest.spots step 0",
        "pest.spots step 1",
        "pest.spots step 2",
    ]


def test_no_named_check_is_approved_gives_an_empty_bundle(
    session_factory: sessionmaker[Session], seed_full: SeedFn
) -> None:
    with session_factory() as session:
        _seed_checks(session, seed_full)
        bundle = get_approved_checks(session, "spath", ["no.such_check"])

    assert bundle is not None
    assert bundle.probes == []


def test_approved_checks_are_none_without_an_approved_set(
    session_factory: sessionmaker[Session], seed_full: SeedFn
) -> None:
    with session_factory() as session:
        seed_full(session, status="draft")
        assert get_approved_checks(session, "spath") is None
        assert get_approved_checks(session, "spath", ["water_deficit.leaf_droop"]) is None
