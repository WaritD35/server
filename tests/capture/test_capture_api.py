"""``/capture`` debug routes: admin only, contract shape, error codes."""

import uuid
from collections.abc import Callable

import pytest
from capture_fakes import FakeStorage, FakeWebcam, dark_image
from fastapi.testclient import TestClient

from capture import service
from core.users.models import User

ClientAs = Callable[[User | None], TestClient]


def _run(client: TestClient, plant_id: uuid.UUID | str) -> tuple[int, dict]:
    response = client.post(f"/capture/plants/{plant_id}/run")
    return response.status_code, response.json()


def test_run_requires_an_admin(
    client_as: ClientAs, admin: User, owner: User, plant_id: uuid.UUID, webcam: FakeWebcam
) -> None:
    assert _run(client_as(None), plant_id)[0] == 401
    assert _run(client_as(owner), plant_id)[0] == 403
    assert client_as(owner).get(f"/capture/plants/{plant_id}/frames").status_code == 403
    assert webcam.calls == []


def test_run_returns_the_capture_and_its_frame(
    client_as: ClientAs,
    admin: User,
    plant_id: uuid.UUID,
    webcam: FakeWebcam,
    storage: FakeStorage,
) -> None:
    code, body = _run(client_as(admin), plant_id)

    assert code == 200
    assert body["plant_id"] == str(plant_id)
    assert body["quality_passed"] is True
    assert body["quality_reason"] is None
    assert isinstance(body["brightness"], float) and isinstance(body["blur_score"], float)
    frame = body["processed_frame"]
    assert frame["capture_id"] == body["capture_id"]
    assert frame["run_id"] is None
    assert frame["crop_type"] == "whole_plant"
    assert frame["object_key"] == f"{plant_id}/2026/10/11/{frame['id']}_whole_plant.jpg"
    assert frame["aligned_to_frame_id"] is None
    assert frame["width"] > 0 and frame["height"] > 0
    assert body["captured_at"].startswith("2026-10-11T07:06")
    assert frame["processed_at"]


def test_quality_failure_answers_200_without_a_frame(
    client_as: ClientAs,
    admin: User,
    plant_id: uuid.UUID,
    webcam: FakeWebcam,
    storage: FakeStorage,
) -> None:
    webcam.image = dark_image()
    code, body = _run(client_as(admin), plant_id)

    assert code == 200
    assert body["quality_passed"] is False
    assert body["quality_reason"] == "image_too_dark,image_too_blurry"
    assert body["processed_frame"] is None


def test_unknown_or_archived_plant_is_404(
    client_as: ClientAs, admin: User, add_plant: Callable[..., uuid.UUID], webcam: FakeWebcam
) -> None:
    client = client_as(admin)
    assert _run(client, uuid.uuid4())[0] == 404
    assert _run(client, add_plant(archived=True))[0] == 404
    assert client.get(f"/capture/plants/{uuid.uuid4()}/frames").status_code == 404
    assert webcam.calls == []


@pytest.mark.parametrize(
    ("webcam_map", "device_id"),
    [("", "cam-1"), ("cam-2=1", "cam-1"), ("cam-1=0", None)],
)
def test_unavailable_capture_is_409(
    client_as: ClientAs,
    admin: User,
    add_plant: Callable[..., uuid.UUID],
    set_webcam_map: Callable[[str], None],
    webcam: FakeWebcam,
    webcam_map: str,
    device_id: str | None,
) -> None:
    set_webcam_map(webcam_map)
    assert _run(client_as(admin), add_plant(device_id))[0] == 409


def test_busy_webcam_is_409(
    client_as: ClientAs, admin: User, plant_id: uuid.UUID, webcam: FakeWebcam
) -> None:
    with service._device_lock("cam-1"):
        code, body = _run(client_as(admin), plant_id)
    assert code == 409
    assert "busy" in body["detail"]


def test_webcam_failure_is_503(
    client_as: ClientAs,
    admin: User,
    plant_id: uuid.UUID,
    webcam: FakeWebcam,
    storage: FakeStorage,
) -> None:
    webcam.fail()
    assert _run(client_as(admin), plant_id)[0] == 503


def test_storage_failure_is_503(
    client_as: ClientAs,
    admin: User,
    plant_id: uuid.UUID,
    webcam: FakeWebcam,
    storage: FakeStorage,
) -> None:
    storage.down = True
    assert _run(client_as(admin), plant_id)[0] == 503


def test_frames_lists_newest_first(
    client_as: ClientAs,
    admin: User,
    plant_id: uuid.UUID,
    webcam: FakeWebcam,
    storage: FakeStorage,
) -> None:
    client = client_as(admin)
    ids = [_run(client, plant_id)[1]["processed_frame"]["id"] for _ in range(3)]

    frames = client.get(f"/capture/plants/{plant_id}/frames").json()
    assert [f["id"] for f in frames] == ids[::-1]
    assert frames[0]["plant_id"] == str(plant_id)
    assert frames[0]["aligned_to_frame_id"] == ids[1]

    limited = client.get(f"/capture/plants/{plant_id}/frames", params={"limit": 1}).json()
    assert [f["id"] for f in limited] == [ids[2]]


@pytest.mark.parametrize("limit", [0, 51])
def test_frames_limit_is_bounded(
    client_as: ClientAs, admin: User, plant_id: uuid.UUID, limit: int
) -> None:
    response = client_as(admin).get(f"/capture/plants/{plant_id}/frames", params={"limit": limit})
    assert response.status_code == 422
