"""Repository layer for the companion API.

Encapsulates all bounded-context data retrieval. Delegates strictly to published
interfaces without direct ORM model imports or cross-schema queries.
"""

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from action.interface import get_last_watered_at as action_get_last_watered_at
from advice.interface import get_latest_care_plan_for_plant as advice_get_latest_care_plan
from advice.schemas import CarePlanRead
from assessment.interface import count_observations as assessment_count_observations
from assessment.interface import get_previous_observation as assessment_get_previous_observation
from assessment.interface import get_trigger_result as assessment_get_trigger_result
from assessment.schemas import ObservationRead, TriggerResult
from companion.interface import get_latest_message as companion_get_latest_message
from companion.schemas import CompanionMessageRead
from knowledge.interface import get_species as knowledge_get_species
from knowledge.schemas import SpeciesRead
from registry.interface import get_plant as registry_get_plant
from registry.interface import get_plant_by_device as registry_get_plant_by_device
from registry.interface import is_plant_owned_by as registry_is_plant_owned_by
from registry.schemas import PlantRead


class CompanionRepository:
    """Read-only data access layer orchestrating calls across bounded context interfaces."""

    def get_plant(self, session: Session, plant_id: uuid.UUID) -> PlantRead | None:
        """Fetch plant identity and gamification properties from registry."""
        return registry_get_plant(session, plant_id)

    def get_plant_by_device(self, session: Session, device_id: str) -> PlantRead | None:
        """Resolve active plant bound to a physical device serial from registry."""
        return registry_get_plant_by_device(session, device_id)

    def is_plant_owned_by(
        self, session: Session, plant_id: uuid.UUID, user_id: uuid.UUID
    ) -> bool:
        """Check user plant ownership in registry."""
        return registry_is_plant_owned_by(session, plant_id, user_id)

    def get_species(self, session: Session, species_code: str) -> SpeciesRead | None:
        """Fetch species catalog entry from knowledge context."""
        return knowledge_get_species(session, species_code)

    def get_latest_observation(
        self, session: Session, plant_id: uuid.UUID
    ) -> ObservationRead | None:
        """Fetch latest processed observation for a plant from assessment context."""
        return assessment_get_previous_observation(session, plant_id)

    def get_trigger_result(
        self, session: Session, run_id: uuid.UUID
    ) -> TriggerResult | None:
        """Fetch deterministic trigger verdict for an orchestrator run from assessment context."""
        return assessment_get_trigger_result(session, run_id)

    def count_observations(self, session: Session, plant_id: uuid.UUID) -> int:
        """Count total observations for a plant from assessment context."""
        return assessment_count_observations(session, plant_id)

    def get_latest_care_plan(
        self, session: Session, plant_id: uuid.UUID
    ) -> CarePlanRead | None:
        """Fetch latest care plan prescribed for a plant from advice context."""
        return advice_get_latest_care_plan(session, plant_id)

    def get_latest_companion_message(
        self, session: Session, plant_id: uuid.UUID
    ) -> CompanionMessageRead | None:
        """Fetch latest companion persona dialogue message from companion context."""
        return companion_get_latest_message(session, plant_id)

    def get_last_watered_at(
        self, session: Session, plant_id: uuid.UUID
    ) -> datetime | None:
        """Fetch timestamp of most recent 'watered' event from action context."""
        return action_get_last_watered_at(session, plant_id)
