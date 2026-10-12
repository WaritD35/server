"""Health Assessment (context 3).

Runs the VLM probe on a previous/current frame pair, stores the observation,
then evaluates deterministic rules and milestones. Owns the ``assessment``
Postgres schema.

Public surface:

- :data:`router` — the ``/assessment`` debug endpoint (superuser), mounted by
  ``main_web``.
- :func:`assess_frames`, :func:`evaluate_observation` — the pipeline's
  assessment stage (VLM probe, then 0-token rules), called by ``orchestrator``.
  :class:`AssessmentSkipped` means the plant cannot be assessed yet.
- :func:`get_trigger_result`, :func:`get_observation_by_run`,
  :func:`get_previous_observation`, … — read interface in
  :mod:`assessment.interface`, importable without the VLM stack.
"""

from assessment.api import router
from assessment.interface import (
    get_observation_by_run,
    get_previous_observation,
    get_trigger_result,
)
from assessment.schemas import ObservationRead, TriggerDecision, TriggerResult
from assessment.service import AssessmentSkipped, assess_frames, evaluate_observation

__all__ = [
    "AssessmentSkipped",
    "ObservationRead",
    "TriggerDecision",
    "TriggerResult",
    "assess_frames",
    "evaluate_observation",
    "get_observation_by_run",
    "get_previous_observation",
    "get_trigger_result",
    "router",
]
