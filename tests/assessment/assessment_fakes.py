"""A fake ``capture`` frame source and a fake VLM probe for the ``assessment`` tests."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from capture.schemas import FramePair, FrameRef
from mlops.assessment import ProbeInput, ProbeVerdict

#: When the current frame of a fake pair was captured.
CURRENT_CAPTURED_AT = datetime(2026, 10, 11, 7, 5, tzinfo=UTC)


def _frame(plant_id: uuid.UUID, key: str, captured_at: datetime) -> FrameRef:
    return FrameRef(
        id=uuid.uuid4(),
        capture_id=uuid.uuid4(),
        plant_id=plant_id,
        run_id=None,
        object_key=key,
        crop_type="whole_plant",
        width=1024,
        height=1024,
        aligned_to_frame_id=None,
        processed_at=captured_at + timedelta(seconds=2),
        bucket="frames-derived",
        captured_at=captured_at,
    )


@dataclass
class FakeFrames:
    """Stands in for ``capture.interface``: a pair per plant, bytes derived from the key."""

    pairs: dict[uuid.UUID, FramePair] = field(default_factory=dict)
    pair_calls: list[tuple[uuid.UUID, uuid.UUID | None]] = field(default_factory=list)
    read_error: Exception | None = None

    def add_pair(self, plant_id: uuid.UUID) -> FramePair:
        pair = FramePair(
            previous=_frame(
                plant_id, f"{plant_id}/prev.jpg", CURRENT_CAPTURED_AT - timedelta(days=1)
            ),
            current=_frame(plant_id, f"{plant_id}/curr.jpg", CURRENT_CAPTURED_AT),
        )
        self.pairs[plant_id] = pair
        return pair

    def get_frame_pair(
        self, _session: Session, plant_id: uuid.UUID, run_id: uuid.UUID | None = None
    ) -> FramePair | None:
        self.pair_calls.append((plant_id, run_id))
        return self.pairs.get(plant_id)

    def read_frame(self, frame: FrameRef) -> bytes:
        if self.read_error is not None:
            raise self.read_error
        return f"jpeg:{frame.object_key}".encode()


def default_verdict() -> ProbeVerdict:
    return ProbeVerdict(
        health_status="possibly_unhealthy",
        observations=[
            {"type": "leaf_yellowing", "severity": "moderate", "description": "Lower leaf"}
        ],
        consensus={"agreement": 0.8, "runs": 5, "model_stated_average": 0.72},
        trace_id="trace-1",
    )


@dataclass
class FakeProbe:
    """Stands in for ``mlops.assessment.run_probe``; ``error`` is raised instead of answering."""

    verdict: ProbeVerdict = field(default_factory=default_verdict)
    error: Exception | None = None
    calls: list[ProbeInput] = field(default_factory=list)

    def __call__(self, probe: ProbeInput) -> ProbeVerdict:
        self.calls.append(probe)
        if self.error is not None:
            raise self.error
        return self.verdict
