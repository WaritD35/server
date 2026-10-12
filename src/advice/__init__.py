"""Care Advice (context 5).

Turns the assessment's trigger verdict into a diagnosis and a ranked care plan.
Owns the ``advice`` Postgres schema.

Public surface:

- :func:`generate_for_plant` — the pipeline's advice stage, called by
  ``orchestrator``; stores a care plan only when the run's trigger asks for one.
- :func:`get_care_plan`, :func:`get_latest_care_plan_for_plant` — read
  interface for the other contexts; plans come back as :class:`CarePlanRead`.
"""

from advice.interface import get_care_plan, get_latest_care_plan_for_plant
from advice.schemas import CarePlanRead
from advice.service import generate_for_plant

__all__ = [
    "CarePlanRead",
    "generate_for_plant",
    "get_care_plan",
    "get_latest_care_plan_for_plant",
]
