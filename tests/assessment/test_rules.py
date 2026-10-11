"""Unit tests for deterministic Event Engine rules (Zero LLM Tokens)."""

import uuid
from datetime import UTC, datetime

from assessment.models import Observation
from assessment.schemas import HealthStatus, TriggerDecision
from assessment.service import evaluate_rules


def make_obs(
    health_status: str,
    symptoms: list[dict] | None = None,
    confidence: float = 1.0,
    agreement: float | None = None,
) -> Observation:
    consensus = {"agreement": agreement, "runs": 5} if agreement is not None else None
    return Observation(
        id=uuid.uuid4(),
        plant_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        timestamp=datetime.now(UTC),
        health_status=health_status,
        confidence=confidence,
        observations_json=symptoms or [],
        consensus_json=consensus,
    )


def test_baseline_healthy_no_action():
    obs = make_obs(HealthStatus.HEALTHY.value, [])
    result = evaluate_rules(obs, prev_obs=None)
    assert result.decision == TriggerDecision.NO_ACTION


def test_baseline_with_symptoms_requires_care():
    obs = make_obs(
        HealthStatus.POSSIBLY_UNHEALTHY.value,
        [{"type": "yellowing", "severity": "mild", "description": "pale leaves"}],
    )
    result = evaluate_rules(obs, prev_obs=None)
    assert result.decision == TriggerDecision.CARE_ADVICE_REQUIRED
    assert result.primary_symptom == "yellowing"


def test_new_symptom_triggers_advice():
    prev = make_obs(HealthStatus.HEALTHY.value, [])
    new_obs = make_obs(
        HealthStatus.HEALTHY.value,
        [{"type": "drooping", "severity": "mild", "description": "drooping"}],
    )
    result = evaluate_rules(new_obs, prev_obs=prev)
    assert result.decision == TriggerDecision.CARE_ADVICE_REQUIRED
    assert result.primary_symptom == "drooping"


def test_severity_escalation_triggers_advice():
    prev = make_obs(
        HealthStatus.POSSIBLY_UNHEALTHY.value,
        [{"type": "yellowing", "severity": "mild", "description": "slight"}],
    )
    new_obs = make_obs(
        HealthStatus.POSSIBLY_UNHEALTHY.value,
        [{"type": "yellowing", "severity": "severe", "description": "deep yellow"}],
    )
    result = evaluate_rules(new_obs, prev_obs=prev)
    assert result.decision == TriggerDecision.CARE_ADVICE_REQUIRED
    assert result.primary_symptom == "yellowing"


def test_health_degradation_triggers_advice():
    prev = make_obs(HealthStatus.HEALTHY.value, [])
    new_obs = make_obs(HealthStatus.UNHEALTHY.value, [])
    result = evaluate_rules(new_obs, prev_obs=prev)
    assert result.decision == TriggerDecision.CARE_ADVICE_REQUIRED


def test_low_confidence_requests_more_info():
    obs = make_obs(HealthStatus.HEALTHY.value, [], confidence=0.35)
    result = evaluate_rules(obs, prev_obs=None, confidence_threshold=0.5)
    assert result.decision == TriggerDecision.REQUEST_MORE_INFORMATION


def test_low_agreement_requests_more_info():
    obs = make_obs(HealthStatus.HEALTHY.value, [], agreement=0.42)
    result = evaluate_rules(obs, prev_obs=None, confidence_threshold=0.5)
    assert result.decision == TriggerDecision.REQUEST_MORE_INFORMATION


def test_health_improvement_no_action():
    prev = make_obs(HealthStatus.UNHEALTHY.value, [])
    new_obs = make_obs(HealthStatus.HEALTHY.value, [])
    result = evaluate_rules(new_obs, prev_obs=prev)
    assert result.decision == TriggerDecision.NO_ACTION


def test_steady_state_no_action():
    prev = make_obs(
        HealthStatus.POSSIBLY_UNHEALTHY.value,
        [{"type": "yellowing", "severity": "mild", "description": "steady"}],
    )
    new_obs = make_obs(
        HealthStatus.POSSIBLY_UNHEALTHY.value,
        [{"type": "yellowing", "severity": "mild", "description": "steady"}],
    )
    result = evaluate_rules(new_obs, prev_obs=prev)
    assert result.decision == TriggerDecision.NO_ACTION
