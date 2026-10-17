"""``/assessment`` debug route: admin only, contract shape, error codes, history effect."""

import uuid
from collections.abc import Callable
from datetime import timedelta

import pytest
from assessment_fakes import CURRENT_CAPTURED_AT, FakeFrames, FakeProbe
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from assessment.interface import count_observations, get_previous_observation
from assessment.models import Observation
from assessment.schemas import HealthStatus, TriggerDecision
from assessment.service import evaluate_observation
from capture.service import CaptureDeviceError
from core.storage import bucket_names
from core.users.models import User
from mlops.assessment import AssessmentRunError, AssessmentUnavailableError, ProbeVerdict

ClientAs = Callable[[User | None], TestClient]


def _run(client: TestClient, plant_id: uuid.UUID | str) -> tuple[int, dict]:
    response = client.post(f"/assessment/plants/{plant_id}/run")
    return response.status_code, response.json()


@pytest.fixture
def ready_plant(
    plant_id: uuid.UUID, add_species: Callable[..., None], frames: FakeFrames
) -> uuid.UUID:
    add_species(knowledge="Keep the soil moist.")
    frames.add_pair(plant_id)
    return plant_id


def test_run_requires_an_admin(
    client_as: ClientAs, owner: User, ready_plant: uuid.UUID, probe: FakeProbe
) -> None:
    assert _run(client_as(None), ready_plant)[0] == 401
    assert _run(client_as(owner), ready_plant)[0] == 403
    assert probe.calls == []


def test_run_assesses_the_latest_pair_and_returns_the_observation(
    client_as: ClientAs,
    admin: User,
    ready_plant: uuid.UUID,
    frames: FakeFrames,
    probe: FakeProbe,
) -> None:
    code, body = _run(client_as(admin), ready_plant)

    assert code == 200
    assert frames.pair_calls == [(ready_plant, None)]
    assert len(probe.calls) == 1
    assert body["plant_id"] == str(ready_plant)
    assert uuid.UUID(body["run_id"])
    assert body["timestamp"].startswith("2026-10-11T07:05:00")
    assert body["health_status"] == "possibly_unhealthy"
    assert body["confidence"] == 0.8
    assert body["observations"] == [
        {"type": "leaf_yellowing", "severity": "moderate", "description": "Lower leaf"}
    ]
    assert body["consensus"] == {"agreement": 0.8, "runs": 5, "model_stated_average": 0.72}
    assert body["probe_set_id"] is None
    assert body["probe_results"] == []
    bucket = bucket_names()["frames_derived"]
    assert body["image_refs"] == [
        f"s3://{bucket}/{ready_plant}/prev.jpg",
        f"s3://{bucket}/{ready_plant}/curr.jpg",
    ]
    assert body["description"] is None
    assert body["companion_message"] is None
    assert set(body) == {
        "id",
        "plant_id",
        "run_id",
        "timestamp",
        "health_status",
        "confidence",
        "observations",
        "consensus",
        "image_refs",
        "probe_set_id",
        "probe_results",
        "description",
        "companion_message",
        "created_at",
    }


def test_each_run_gets_a_fresh_run_id(
    client_as: ClientAs, admin: User, ready_plant: uuid.UUID, probe: FakeProbe
) -> None:
    first = _run(client_as(admin), ready_plant)[1]["run_id"]
    second = _run(client_as(admin), ready_plant)[1]["run_id"]

    assert first != second


def test_unknown_plant_is_404(client_as: ClientAs, admin: User, probe: FakeProbe) -> None:
    assert _run(client_as(admin), uuid.uuid4())[0] == 404
    assert probe.calls == []


def test_archived_plant_is_404(
    client_as: ClientAs,
    admin: User,
    add_plant: Callable[..., uuid.UUID],
    frames: FakeFrames,
    probe: FakeProbe,
) -> None:
    plant_id = add_plant(archived=True)
    frames.add_pair(plant_id)

    assert _run(client_as(admin), plant_id)[0] == 404
    assert probe.calls == []
    assert _run(client_as(admin), "not-a-uuid")[0] == 422
    assert probe.calls == []


def test_plant_without_species_is_409(
    client_as: ClientAs,
    admin: User,
    add_plant: Callable[..., uuid.UUID],
    frames: FakeFrames,
    probe: FakeProbe,
) -> None:
    plant_id = add_plant(species_code=None)
    frames.add_pair(plant_id)

    code, body = _run(client_as(admin), plant_id)

    assert code == 409
    assert body["detail"] == "species not set"


def test_fewer_than_two_frames_is_409(
    client_as: ClientAs,
    admin: User,
    plant_id: uuid.UUID,
    add_species: Callable[..., None],
    frames: FakeFrames,
    probe: FakeProbe,
) -> None:
    add_species()

    code, body = _run(client_as(admin), plant_id)

    assert code == 409
    assert body["detail"] == "baseline frame stored; need two frames"
    assert probe.calls == []


def test_majority_invalid_runs_is_502(
    client_as: ClientAs, admin: User, ready_plant: uuid.UUID, probe: FakeProbe
) -> None:
    probe.error = AssessmentRunError(5, 2, ValueError("bad json"))

    code, body = _run(client_as(admin), ready_plant)

    assert code == 502
    assert "only 2 of 5" in body["detail"]


@pytest.mark.parametrize(
    "error",
    [
        AssessmentUnavailableError("cannot fetch Langfuse prompt"),
        AssessmentUnavailableError("the VLM backend is unreachable"),
    ],
)
def test_assessment_unavailable_is_503(
    client_as: ClientAs,
    admin: User,
    ready_plant: uuid.UUID,
    probe: FakeProbe,
    error: Exception,
) -> None:
    probe.error = error

    code, body = _run(client_as(admin), ready_plant)

    assert code == 503
    assert body["detail"] == str(error)


def test_object_storage_failure_reading_frames_is_503(
    client_as: ClientAs,
    admin: User,
    ready_plant: uuid.UUID,
    frames: FakeFrames,
    probe: FakeProbe,
) -> None:
    frames.read_error = CaptureDeviceError("object storage is unavailable")

    code, _body = _run(client_as(admin), ready_plant)

    assert code == 503
    assert probe.calls == []


def test_a_debug_observation_is_history_for_the_next_scheduled_evaluation(
    client_as: ClientAs,
    admin: User,
    session: Session,
    ready_plant: uuid.UUID,
    probe: FakeProbe,
) -> None:
    probe.verdict = ProbeVerdict(
        health_status="unhealthy",
        observations=[{"type": "wilting", "severity": "severe", "description": "Drooping"}],
        consensus={"agreement": 1.0, "runs": 5, "model_stated_average": 0.9},
    )
    debug = _run(client_as(admin), ready_plant)[1]

    previous = get_previous_observation(session, ready_plant)
    assert previous is not None and str(previous.id) == debug["id"]
    assert (
        get_previous_observation(
            session, ready_plant, excluding_run_id=uuid.UUID(debug["run_id"])
        )
        is None
    )
    assert count_observations(session, ready_plant) == 1

    scheduled_run = uuid.uuid4()
    session.add(
        Observation(
            plant_id=ready_plant,
            run_id=scheduled_run,
            timestamp=CURRENT_CAPTURED_AT + timedelta(hours=5),
            health_status=HealthStatus.HEALTHY.value,
            confidence=1.0,
            observations_json=[],
            consensus_json={"agreement": 1.0, "runs": 5, "model_stated_average": 0.9},
        )
    )
    session.commit()

    prior = get_previous_observation(session, ready_plant, excluding_run_id=scheduled_run)
    assert prior is not None and str(prior.id) == debug["id"]

    record = evaluate_observation(session, ready_plant, scheduled_run)

    assert record.decision == TriggerDecision.NO_ACTION.value
    assert record.reasoning == "Health status improved from unhealthy to healthy."
    assert count_observations(session, ready_plant) == 2
