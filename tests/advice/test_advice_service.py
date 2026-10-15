"""Integration tests for advice service layer."""

import dataclasses
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from advice import service as advice_service
from advice.interface import get_care_plan, get_latest_care_plan_for_plant
from advice.service import generate_for_plant
from assessment.models import Observation, TriggerResultRecord
from assessment.schemas import HealthStatus, TriggerDecision
from knowledge.models import Probe, ProbeAction, ProbeSet, ResearchDocument, Species
from mlops.advice import AdviceInput
from registry.models import Plant


def test_generate_advice_skipped_when_no_action(session: Session):
    plant_id = uuid.uuid4()
    run_id = uuid.uuid4()

    tr = TriggerResultRecord(
        run_id=run_id,
        plant_id=plant_id,
        decision=TriggerDecision.NO_ACTION.value,
        confidence=1.0,
    )
    session.add(tr)
    session.commit()

    plan = generate_for_plant(session, plant_id, run_id)
    assert plan is None


def test_generate_advice_creates_plan_on_care_advice_required(session: Session):
    user_id = uuid.uuid4()
    plant_id = uuid.uuid4()
    run_id = uuid.uuid4()

    # Plant
    plant = Plant(
        id=plant_id,
        owner_id=user_id,
        name="Monty",
        species_code="monstera_deliciosa",
    )
    session.add(plant)

    # Trigger verdict
    tr = TriggerResultRecord(
        run_id=run_id,
        plant_id=plant_id,
        decision=TriggerDecision.CARE_ADVICE_REQUIRED.value,
        primary_symptom="yellowing",
        confidence=0.95,
        reasoning="Health status dropped.",
    )
    session.add(tr)

    # Observation
    obs = Observation(
        id=uuid.uuid4(),
        plant_id=plant_id,
        run_id=run_id,
        timestamp=datetime.now(UTC),
        health_status=HealthStatus.UNHEALTHY.value,
        confidence=0.95,
        observations_json=[{"type": "yellowing", "severity": "severe"}],
    )
    session.add(obs)
    session.commit()

    plan = generate_for_plant(session, plant_id, run_id)
    assert plan is not None
    assert plan.plant_id == plant_id
    assert plan.run_id == run_id
    # Assert normalized relational actions are created
    assert len(plan.actions) > 0
    assert plan.actions[0].action_id.startswith("act_")
    assert plan.actions[0].care_plan_pk == plan.id
    assert plan.actions[0].care_plan_id == plan.care_plan_id

    # Test published interface
    fetched = get_care_plan(session, run_id)
    assert fetched is not None
    assert fetched.care_plan_id == plan.care_plan_id
    assert len(fetched.actions) == len(plan.actions)
    assert fetched.actions[0].id == plan.actions[0].action_id

    latest = get_latest_care_plan_for_plant(session, plant_id)
    assert latest is not None
    assert latest.id == plan.id


# --------------------------------------------------------------------------- #
# only approved knowledge reaches the plan
# --------------------------------------------------------------------------- #


def _seed_care_case(
    session: Session, *, fired: list[str], approved_probes: list[str] | None
) -> tuple[uuid.UUID, uuid.UUID]:
    """A plant, its trigger and observation (with fired checks), and optionally an
    approved probe set whose probes each carry one action. Returns (plant_id, run_id)."""
    plant_id, run_id = uuid.uuid4(), uuid.uuid4()
    session.add(Species(species_code="spath", scientific_name="Spathiphyllum wallisii"))
    session.flush()
    document = ResearchDocument(
        species_code="spath", title="care", body="SECRET RESEARCH TEXT", author="alice"
    )
    session.add(document)
    session.flush()
    if approved_probes is not None:
        probe_set = ProbeSet(
            research_document_id=document.id,
            species_code="spath",
            source_content_hash=document.content_hash,
            llm_model="test-model",
            status="approved",
        )
        session.add(probe_set)
        session.flush()
        for priority, slug in enumerate(approved_probes, start=1):
            probe = Probe(
                probe_set_id=probe_set.id,
                slug=slug,
                care_need=slug.split(".")[0],
                priority=priority,
                is_screening=False,
                question=f"Is there {slug}?",
                worse_looks_like="worse",
                better_looks_like="better",
                not_this="look-alike",
                evidence_quote=f"Research says {slug} needs care.",
            )
            session.add(probe)
            session.flush()
            session.add(
                ProbeAction(
                    probe_id=probe.id,
                    ordering=0,
                    instruction=f"Approved step for {slug}.",
                    urgency="soon",
                    expect_typical_hours=24,
                    expect_max_hours=72,
                )
            )
    session.add(Plant(id=plant_id, owner_id=uuid.uuid4(), name="Monty", species_code="spath"))
    session.add(
        TriggerResultRecord(
            run_id=run_id,
            plant_id=plant_id,
            decision=TriggerDecision.CARE_ADVICE_REQUIRED.value,
            confidence=0.9,
            reasoning="Check fired.",
        )
    )
    session.add(
        Observation(
            plant_id=plant_id,
            run_id=run_id,
            timestamp=datetime.now(UTC),
            health_status=HealthStatus.POSSIBLY_UNHEALTHY.value,
            confidence=0.9,
            observations_json=[],
            probe_results_json=[
                {"slug": s, "answer": "yes", "fired": True, "care_need": s.split(".")[0],
                 "question": f"Is there {s}?", "evidence": "seen"}
                for s in fired
            ],
        )
    )
    session.commit()
    return plant_id, run_id


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[AdviceInput]:
    """Swap the LLM call for one that records its input and returns a no-knowledge plan."""
    calls: list[AdviceInput] = []
    real = advice_service.generate_advice

    def fake(payload: AdviceInput) -> Any:
        calls.append(payload)
        return real(dataclasses.replace(payload, approved_actions=()))

    monkeypatch.setattr(advice_service, "generate_advice", fake)
    return calls


def test_only_the_fired_checks_actions_are_offered(
    session: Session, captured: list[AdviceInput]
) -> None:
    plant_id, run_id = _seed_care_case(
        session,
        fired=["water_excess.soggy_soil"],
        approved_probes=["water_deficit.leaf_droop", "water_excess.soggy_soil"],
    )

    generate_for_plant(session, plant_id, run_id)

    (payload,) = captured
    assert [c.slug for c in payload.fired_checks] == ["water_excess.soggy_soil"]
    assert [a.instruction for a in payload.approved_actions] == [
        "Approved step for water_excess.soggy_soil."
    ]
    assert "SECRET RESEARCH TEXT" not in repr(payload)


def test_with_no_fired_check_every_approved_action_is_offered(
    session: Session, captured: list[AdviceInput]
) -> None:
    plant_id, run_id = _seed_care_case(
        session, fired=[], approved_probes=["a_need.first", "b_need.second"]
    )

    generate_for_plant(session, plant_id, run_id)

    (payload,) = captured
    assert payload.fired_checks == ()
    assert [a.check_slug for a in payload.approved_actions] == ["a_need.first", "b_need.second"]


def test_without_an_approved_set_nothing_is_offered_and_no_text_is_sent(
    session: Session, captured: list[AdviceInput]
) -> None:
    plant_id, run_id = _seed_care_case(session, fired=[], approved_probes=None)

    plan = generate_for_plant(session, plant_id, run_id)

    (payload,) = captured
    assert payload.approved_actions == ()
    assert "SECRET RESEARCH TEXT" not in repr(payload)
    assert plan is not None
    assert [a.action_type for a in plan.actions] == ["inspect"]
    assert plan.actions[0].approved_action_id is None


def test_a_fired_check_carries_its_approved_basis_from_knowledge(
    session: Session, captured: list[AdviceInput]
) -> None:
    plant_id, run_id = _seed_care_case(
        session,
        fired=["water_excess.soggy_soil", "gone.from_the_set"],
        approved_probes=["water_deficit.leaf_droop", "water_excess.soggy_soil"],
    )

    generate_for_plant(session, plant_id, run_id)

    (payload,) = captured
    (check,) = payload.fired_checks
    assert check.slug == "water_excess.soggy_soil"
    assert check.evidence == "seen"
    assert check.basis == "Research says water_excess.soggy_soil needs care."
    assert check.not_this == "look-alike"
    assert "water_deficit.leaf_droop" not in repr(payload.fired_checks)
    assert "SECRET RESEARCH TEXT" not in repr(payload)


def test_each_step_records_the_approved_action_it_came_from(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    plant_id, run_id = _seed_care_case(
        session, fired=["water_excess.soggy_soil"], approved_probes=["water_excess.soggy_soil"]
    )
    monkeypatch.setattr(
        "mlops.factory.get_llm", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("down"))
    )

    plan = generate_for_plant(session, plant_id, run_id)

    assert plan is not None
    (step,) = plan.actions
    assert step.action == "Approved step for water_excess.soggy_soil."
    assert step.approved_action_id is not None
    assert (step.check_slug, step.urgency, step.expect_typical_hours, step.expect_max_hours) == (
        "water_excess.soggy_soil",
        "soon",
        24,
        72,
    )
    (read,) = get_care_plan(session, run_id).actions  # type: ignore[union-attr]
    assert read.approved_action_id == step.approved_action_id
    assert read.expect_max_hours == 72