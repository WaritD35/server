"""The loop that queues runs on schedule and works through them.

One :func:`tick` is a complete pass:

1. **reap** — a run left ``running`` past ``stale_after`` (its process stopped
   mid-stage) is marked ``failed``;
2. **queue** — every live plant gets a run for each schedule slot that is due;
3. **work** — take queued runs one at a time and call their stages in order.

:class:`OrchestratorLoop` calls :func:`tick` every ``poll_seconds`` in a
background thread; ``main_web`` starts it.

PoC: a stage that raises marks its run ``failed`` (with the error in
``detail``) and the loop moves on to the next plant — nothing is retried.
"""

import logging
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from orchestrator import service
from orchestrator.config import OrchestratorConfig, load_config
from orchestrator.db import as_utc, utcnow
from orchestrator.models import PipelineRun
from orchestrator.stages import STAGES, Stage, StageInput, StageSkipped

logger = logging.getLogger(__name__)

PlantIdsFn = Callable[[Session], Sequence[uuid.UUID]]


def _live_plant_ids(session: Session) -> Sequence[uuid.UUID]:
    from registry import list_plant_ids

    return list_plant_ids(session)


@dataclass
class TickReport:
    #: Runs left ``running`` by a stopped process, now marked ``failed``.
    abandoned: int = 0
    queued: int = 0
    finished: dict[str, int] = field(default_factory=dict)


def execute_run(
    session: Session,
    run: PipelineRun,
    *,
    now: Callable[[], datetime] = utcnow,
    stages: Sequence[Stage] = STAGES,
) -> str:
    """Call every stage of ``run`` in order; returns the status it ended with."""
    payload = StageInput(
        run_id=run.id, plant_id=run.plant_id, scheduled_for=as_utc(run.scheduled_for)
    )
    for stage in stages:
        run.current_stage = stage.name
        session.commit()
        try:
            stage.fn(payload)
        except StageSkipped as exc:
            service.finish_run(
                session, run, "skipped", now(), detail=f"{stage.name}: {exc or 'nothing to do'}"
            )
            return "skipped"
        except Exception as exc:
            logger.exception("stage %s failed for plant %s", stage.name, run.plant_id)
            service.finish_run(
                session, run, "failed", now(), detail=f"{stage.name}: {exc!r}"
            )
            return "failed"
    service.finish_run(session, run, "succeeded", now())
    return "succeeded"


def tick(
    session_factory: sessionmaker[Session],
    config: OrchestratorConfig,
    *,
    now: Callable[[], datetime] = utcnow,
    plant_ids: PlantIdsFn = _live_plant_ids,
    stages: Sequence[Stage] = STAGES,
    should_stop: Callable[[], bool] = lambda: False,
) -> TickReport:
    report = TickReport()
    with session_factory() as session:
        report.abandoned = service.fail_stale_runs(session, now(), config.stale_after)
        report.queued = service.enqueue_due(
            session, config, now(), plant_ids(session), stages=stages
        )
    while not should_stop():
        with session_factory() as session:
            run = service.claim_next(session, now())
            if run is None:
                break
            status = execute_run(session, run, now=now, stages=stages)
        report.finished[status] = report.finished.get(status, 0) + 1
    return report


class OrchestratorLoop:
    """Runs :func:`tick` every ``poll_seconds`` on a daemon thread.

    The config file is re-read before every tick, so schedule edits apply
    without a restart.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        config_loader: Callable[[], OrchestratorConfig] = load_config,
    ) -> None:
        self._session_factory = session_factory
        self._config_loader = config_loader
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="orchestrator", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10) -> None:
        """Ask the loop to stop after the run in progress, and wait a little."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            config = self._config_loader()
            if config.enabled:
                report = tick(self._session_factory, config, should_stop=self._stop.is_set)
                if report.abandoned or report.queued or report.finished:
                    logger.info("orchestrator tick: %s", report)
            self._stop.wait(config.poll_interval.total_seconds())
