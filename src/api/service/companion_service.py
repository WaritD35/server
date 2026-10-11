"""Service layer for the companion API.

Orchestrates business logic and aggregates data across domain repositories to produce
the final CompanionStateData model.
"""

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from api.exception.api_exception import (
    DeviceNotBoundError,
    ForbiddenError,
    PlantNotFoundError,
)
from api.model.companion_state import (
    CarePlanActionResponse,
    CarePlanResponse,
    CompanionStateData,
)
from api.repository.companion_repository import CompanionRepository


class CompanionService:
    """Business service synthesizing companion state for edge devices and web clients."""

    def __init__(self, repository: CompanionRepository | None = None) -> None:
        self.repo = repository or CompanionRepository()

    def resolve_plant_for_device(self, session: Session, device_id: str) -> uuid.UUID:
        """Resolve the live plant bound to a physical device identifier."""
        plant = self.repo.get_plant_by_device(session, device_id)
        if plant is None:
            raise DeviceNotBoundError(device_id=device_id)
        return plant.id

    def verify_plant_access(
        self, session: Session, plant_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        """Verify that the authenticated user owns the requested plant."""
        # First ensure plant exists so we raise 404 rather than 403 for non-existent IDs
        plant = self.repo.get_plant(session, plant_id)
        if plant is None:
            raise PlantNotFoundError(plant_id=plant_id)

        if not self.repo.is_plant_owned_by(session, plant_id, user_id):
            raise ForbiddenError(plant_id=plant_id)

    def get_companion_state(
        self, session: Session, plant_id: uuid.UUID
    ) -> CompanionStateData:
        """Synthesize and assemble the real-time companion state for a plant."""
        # 1. Plant identity and gamification (registry)
        plant = self.repo.get_plant(session, plant_id)
        if plant is None:
            raise PlantNotFoundError(plant_id=plant_id)

        # 2. Species botanical display name (knowledge)
        species_name = "Unknown"
        if plant.species_code:
            species_read = self.repo.get_species(session, plant.species_code)
            if species_read:
                species_name = (
                    species_read.scientific_name
                    or species_read.common_name
                    or plant.species_code
                )
            else:
                species_name = plant.species_code

        # 3. Latest observation & deterministic trigger decision (assessment)
        obs = self.repo.get_latest_observation(session, plant_id)
        health_status = "healthy"
        decision = "NO_ACTION"
        timestamp = plant.created_at

        if obs is not None:
            timestamp = obs.timestamp
            health_status = (
                obs.health_status.value
                if hasattr(obs.health_status, "value")
                else str(obs.health_status)
            )
            trigger_result = self.repo.get_trigger_result(session, obs.run_id)
            if trigger_result is not None:
                decision = (
                    trigger_result.decision.value
                    if hasattr(trigger_result.decision, "value")
                    else str(trigger_result.decision)
                )

        # 4. Observation count / dayCount (assessment)
        obs_count = self.repo.count_observations(session, plant_id)
        day_count = max(1, obs_count)

        # 5. Last watered timestamp (action)
        watered_timestamp: datetime | None = self.repo.get_last_watered_at(
            session, plant_id
        )

        # 6. Companion dialogue speech bubble (companion)
        companion_msg_read = self.repo.get_latest_companion_message(session, plant_id)
        if companion_msg_read and companion_msg_read.message:
            companion_message = companion_msg_read.message
        else:
            companion_message = "I'm doing great today! 🌱"

        # 7. Care plan details (advice) — only on CARE_ADVICE_REQUIRED
        care_plan: CarePlanResponse | None = None
        if decision == "CARE_ADVICE_REQUIRED":
            care_plan_read = self.repo.get_latest_care_plan(session, plant_id)
            if care_plan_read is not None:
                actions = [
                    CarePlanActionResponse(
                        id=act.id,
                        priority=act.priority,
                        action=act.action,
                        label=act.label,
                        type=act.type.value if hasattr(act.type, "value") else str(act.type),
                    )
                    for act in care_plan_read.actions
                ]
                care_plan = CarePlanResponse(
                    id=care_plan_read.care_plan_id,
                    status_label=care_plan_read.status_label,
                    assessment=care_plan_read.assessment,
                    actions=actions,
                )

        return CompanionStateData(
            plant_id=str(plant_id),
            name=plant.name,
            species=species_name,
            dayCount=day_count,
            timestamp=timestamp,
            wateredTimestamp=watered_timestamp,
            level=plant.level,
            xpRatio=plant.xp_ratio,
            health_status=health_status,
            decision=decision,
            companion_message=companion_message,
            care_plan=care_plan,
        )
