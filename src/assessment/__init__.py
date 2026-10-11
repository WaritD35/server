"""Health Assessment (context 3).

Runs the VLM probe on a previous/current frame pair, stores the observation,
then evaluates deterministic rules and milestones. Owns the ``assessment``
Postgres schema.

Public surface:

- :data:`router` — the ``/assessment`` debug endpoint (superuser), mounted by
  ``main_web``.
"""

from assessment.api import router

__all__ = ["router"]
