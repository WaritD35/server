"""Service layer for assessment: deterministic rule evaluation & milestone detection."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from assessment.models import Observation, PlantMilestone, TriggerResultRecord
from assessment.schemas import (
    HealthStatus,
    MilestoneType,
    TriggerDecision,
    TriggerResult,
)

SEVERITY_RANKS = {
    "mild": 1,
    "moderate": 2,
    "severe": 3,
}

HEALTH_STATUS_RANKS = {
    HealthStatus.HEALTHY: 1,
    HealthStatus.POSSIBLY_UNHEALTHY: 2,
    HealthStatus.UNHEALTHY: 3,
}


def evaluate_rules(
    new_obs: Observation,
    prev_obs: Observation | None = None,
    confidence_threshold: float = 0.5,
) -> TriggerResult:
    """Deterministic Event Engine rules per PRD §4.2 (Zero LLM Tokens)."""
    # 1. Consensus agreement check
    if new_obs.consensus_json is not None and "agreement" in new_obs.consensus_json:
        agreement = new_obs.consensus_json.get("agreement", 1.0)
        if agreement < confidence_threshold:
            return TriggerResult(
                decision=TriggerDecision.REQUEST_MORE_INFORMATION,
                confidence=agreement,
                reasoning=(
                    f"VLM agreement {agreement:.2f} is below threshold {confidence_threshold:.2f}."
                ),
            )
    elif new_obs.confidence < confidence_threshold:
        return TriggerResult(
            decision=TriggerDecision.REQUEST_MORE_INFORMATION,
            confidence=new_obs.confidence,
            reasoning=(
                f"Confidence {new_obs.confidence:.2f} is below threshold "
                f"{confidence_threshold:.2f}."
            ),
        )

    observations_list = (
        new_obs.observations_json if isinstance(new_obs.observations_json, list) else []
    )

    # 2. First observation check
    if prev_obs is None:
        if new_obs.health_status == HealthStatus.HEALTHY.value and len(observations_list) == 0:
            return TriggerResult(
                decision=TriggerDecision.NO_ACTION,
                reasoning="Baseline observation recorded as healthy with no symptoms.",
            )
        primary = observations_list[0].get("type") if observations_list else "unhealthy_status"
        return TriggerResult(
            decision=TriggerDecision.CARE_ADVICE_REQUIRED,
            primary_symptom=primary,
            reasoning="Initial observation detected symptoms or non-healthy status.",
        )

    # 3. Check for health status degradation
    new_health = HealthStatus(new_obs.health_status)
    prev_health = HealthStatus(prev_obs.health_status)
    new_rank = HEALTH_STATUS_RANKS[new_health]
    prev_rank = HEALTH_STATUS_RANKS[prev_health]

    if new_rank > prev_rank:
        primary = observations_list[0].get("type") if observations_list else None
        return TriggerResult(
            decision=TriggerDecision.CARE_ADVICE_REQUIRED,
            primary_symptom=primary,
            reasoning=f"Health status worsened from {prev_health.value} to {new_health.value}.",
        )

    # 4. Check for new symptom type
    prev_obs_list = (
        prev_obs.observations_json if isinstance(prev_obs.observations_json, list) else []
    )
    prev_symptoms_by_type = {
        obs.get("type"): obs for obs in prev_obs_list if isinstance(obs, dict)
    }

    for obs in observations_list:
        if isinstance(obs, dict):
            s_type = obs.get("type")
            if s_type and s_type not in prev_symptoms_by_type:
                return TriggerResult(
                    decision=TriggerDecision.CARE_ADVICE_REQUIRED,
                    primary_symptom=s_type,
                    reasoning=f"New symptom '{s_type}' appeared (severity: {obs.get('severity')}).",
                )

    # 5. Check for severity escalation
    for obs in observations_list:
        if isinstance(obs, dict):
            s_type = obs.get("type")
            if s_type and s_type in prev_symptoms_by_type:
                prev_item = prev_symptoms_by_type[s_type]
                new_s_rank = SEVERITY_RANKS.get(obs.get("severity", ""), 0)
                prev_s_rank = SEVERITY_RANKS.get(prev_item.get("severity", ""), 0)
                if new_s_rank > prev_s_rank:
                    return TriggerResult(
                        decision=TriggerDecision.CARE_ADVICE_REQUIRED,
                        primary_symptom=s_type,
                        reasoning=(
                            f"Severity of symptom '{s_type}' increased from "
                            f"{prev_item.get('severity')} to {obs.get('severity')}."
                        ),
                    )

    # 6. Improved or stable
    if new_rank < prev_rank:
        return TriggerResult(
            decision=TriggerDecision.NO_ACTION,
            reasoning=f"Health status improved from {prev_health.value} to {new_health.value}.",
        )

    return TriggerResult(
        decision=TriggerDecision.NO_ACTION,
        reasoning="No new symptoms, no severity increase, and health status is stable.",
    )


def detect_milestones(
    new_obs: Observation,
    history: list[Observation],
    existing_milestones: list[PlantMilestone],
) -> list[PlantMilestone]:
    """Detect major life events deterministically based on health trajectory."""
    newly_detected: list[PlantMilestone] = []
    recorded_types = {m.event_type for m in existing_milestones}
    obs_list = new_obs.observations_json if isinstance(new_obs.observations_json, list) else []

    # 1. FIRST_SYMPTOM
    has_symptoms = len(obs_list) > 0 or new_obs.health_status != HealthStatus.HEALTHY.value
    if has_symptoms and MilestoneType.FIRST_SYMPTOM.value not in recorded_types:
        symptom_names = (
            ", ".join(o.get("type", "") for o in obs_list if isinstance(o, dict))
            or "declining health"
        )
        newly_detected.append(
            PlantMilestone(
                plant_id=new_obs.plant_id,
                run_id=new_obs.run_id,
                timestamp=new_obs.timestamp,
                event_type=MilestoneType.FIRST_SYMPTOM.value,
                description=f"First health symptoms detected: {symptom_names}.",
            )
        )

    # 2. HEALTH_CRISIS
    prev_obs = history[-1] if history else None
    prev_was_unhealthy = (
        prev_obs is not None and prev_obs.health_status == HealthStatus.UNHEALTHY.value
    )
    is_now_unhealthy = new_obs.health_status == HealthStatus.UNHEALTHY.value

    if is_now_unhealthy and not prev_was_unhealthy:
        newly_detected.append(
            PlantMilestone(
                plant_id=new_obs.plant_id,
                run_id=new_obs.run_id,
                timestamp=new_obs.timestamp,
                event_type=MilestoneType.HEALTH_CRISIS.value,
                description=f"Entered critical unhealthy state at {new_obs.timestamp.date()}.",
            )
        )

    # Streak calculation
    streak = 0
    if is_now_unhealthy:
        streak = 1
        for past_obs in reversed(history):
            if past_obs.health_status == HealthStatus.UNHEALTHY.value:
                streak += 1
            else:
                break

    # 3. SEVERE_EPISODE (3 days)
    if streak >= 3:
        latest_crisis_ts = max(
            (
                m.timestamp
                for m in existing_milestones
                if m.event_type == MilestoneType.HEALTH_CRISIS.value
            ),
            default=None,
        )
        already_recorded = any(
            m.event_type == MilestoneType.SEVERE_EPISODE.value
            and (latest_crisis_ts is None or m.timestamp >= latest_crisis_ts)
            for m in existing_milestones + newly_detected
        )
        if not already_recorded:
            newly_detected.append(
                PlantMilestone(
                    plant_id=new_obs.plant_id,
                    run_id=new_obs.run_id,
                    timestamp=new_obs.timestamp,
                    event_type=MilestoneType.SEVERE_EPISODE.value,
                    description=f"Persistent severe health crisis for {streak} consecutive days.",
                )
            )

    # 4. NEAR_DEATH (7 days)
    if streak >= 7:
        already_recorded_near_death = any(
            m.event_type == MilestoneType.NEAR_DEATH.value
            and (latest_crisis_ts is None or m.timestamp >= latest_crisis_ts)
            for m in existing_milestones + newly_detected
        )
        if not already_recorded_near_death:
            newly_detected.append(
                PlantMilestone(
                    plant_id=new_obs.plant_id,
                    run_id=new_obs.run_id,
                    timestamp=new_obs.timestamp,
                    event_type=MilestoneType.NEAR_DEATH.value,
                    description=(
                        f"Critical near-death condition: "
                        f"unresolved unhealthy state for {streak} days."
                    ),
                )
            )

    # 5. FULL_RECOVERY
    is_now_healthy = new_obs.health_status == HealthStatus.HEALTHY.value
    was_sick = prev_obs is not None and prev_obs.health_status in (
        HealthStatus.UNHEALTHY.value,
        HealthStatus.POSSIBLY_UNHEALTHY.value,
    )
    had_crisis = any(
        m.event_type in (MilestoneType.HEALTH_CRISIS.value, MilestoneType.SEVERE_EPISODE.value)
        for m in existing_milestones
    )
    if is_now_healthy and was_sick and had_crisis:
        last_crisis_ts = max(
            (
                m.timestamp
                for m in existing_milestones
                if m.event_type
                in (MilestoneType.HEALTH_CRISIS.value, MilestoneType.SEVERE_EPISODE.value)
            ),
            default=None,
        )
        last_recovery_ts = max(
            (
                m.timestamp
                for m in existing_milestones
                if m.event_type == MilestoneType.FULL_RECOVERY.value
            ),
            default=None,
        )
        if last_crisis_ts and (last_recovery_ts is None or last_crisis_ts > last_recovery_ts):
            newly_detected.append(
                PlantMilestone(
                    plant_id=new_obs.plant_id,
                    run_id=new_obs.run_id,
                    timestamp=new_obs.timestamp,
                    event_type=MilestoneType.FULL_RECOVERY.value,
                    description=(
                        f"Full recovery back to healthy status on {new_obs.timestamp.date()}."
                    ),
                    resolved_at=new_obs.timestamp,
                )
            )

    return newly_detected


def evaluate_observation(
    session: Session,
    plant_id: uuid.UUID,
    run_id: uuid.UUID,
) -> TriggerResultRecord:
    """Execute assessment pipeline stage: evaluate rules and record milestones."""
    stmt = (
        select(Observation)
        .where(Observation.run_id == run_id)
        .order_by(Observation.created_at.desc())
        .limit(1)
    )
    new_obs = session.scalars(stmt).first()
    if new_obs is None:
        raise ValueError(f"No observation found for run_id {run_id}")

    # Fetch previous observation
    prev_stmt = (
        select(Observation)
        .where(Observation.plant_id == plant_id, Observation.id != new_obs.id)
        .order_by(Observation.timestamp.desc())
        .limit(1)
    )
    prev_obs = session.scalars(prev_stmt).first()

    # 1. Deterministic evaluation
    tr = evaluate_rules(new_obs, prev_obs)

    # Save TriggerResultRecord
    record = TriggerResultRecord(
        run_id=run_id,
        plant_id=plant_id,
        decision=tr.decision.value,
        primary_symptom=tr.primary_symptom,
        confidence=tr.confidence,
        reasoning=tr.reasoning,
    )
    session.add(record)

    # 2. Milestone detection
    hist_stmt = (
        select(Observation)
        .where(Observation.plant_id == plant_id, Observation.id != new_obs.id)
        .order_by(Observation.timestamp.asc())
    )
    history = list(session.scalars(hist_stmt).all())

    ms_stmt = (
        select(PlantMilestone)
        .where(PlantMilestone.plant_id == plant_id)
        .order_by(PlantMilestone.timestamp.asc())
    )
    existing_milestones = list(session.scalars(ms_stmt).all())

    new_milestones = detect_milestones(new_obs, history, existing_milestones)
    for m in new_milestones:
        session.add(m)

    session.commit()
    session.refresh(record)
    return record
