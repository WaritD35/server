"""``/capture`` routes: device upload, admin upload and listing; auth and error codes."""

import uuid
from collections.abc import Callable

from capture_fakes import FakeStorage, dark_jpeg, plant_jpeg
from fastapi.testclient import TestClient

from core.devices.models import Device
from core.users.models import User

ClientAs = Callable[[User | Device | None], TestClient]


def _device_upload(
    client: TestClient, jpeg: bytes, **form: str
) -> tuple[int, dict]:
    response = client.post(
        "/capture/devices/me/frames",
        files={"image": ("photo.jpg", jpeg, "image/jpeg")},
        data=form,
    )
    return response.status_code, response.json()


def _admin_upload(client: TestClient, plant_id: uuid.UUID | str, jpeg: bytes) -> tuple[int, dict]:
    response = client.post(
        f"/capture/plants/{plant_id}/frames",
        files={"image": ("photo.jpg", jpeg, "image/jpeg")},
    )
    return response.status_code, response.json()


# --------------------------------------------------------------------------- #
# device upload
# --------------------------------------------------------------------------- #


def test_a_device_uploads_a_photo_of_its_plant(
    client_as: ClientAs,
    plant_id: uuid.UUID,
    add_device: Callable[..., Device],
    storage: FakeStorage,
) -> None:
    device = add_device("cam-1")

    code, body = _device_upload(
        client_as(device), plant_jpeg(), captured_at="2026-10-11T07:00:00Z"
    )

    assert code == 201
    assert body["plant_id"] == str(plant_id)
    assert body["quality_passed"] is True
    assert body["quality_reason"] is None
    assert body["captured_at"].startswith("2026-10-11T07:00:00")
    frame = body["processed_frame"]
    assert frame["plant_id"] == str(plant_id)
    assert frame["run_id"] is None
    assert frame["crop_type"] == "whole_plant"


def test_a_bad_photo_answers_201_with_the_reason_to_retake(
    client_as: ClientAs,
    plant_id: uuid.UUID,
    add_device: Callable[..., Device],
    storage: FakeStorage,
) -> None:
    code, body = _device_upload(client_as(add_device("cam-1")), dark_jpeg())

    assert code == 201
    assert body["quality_passed"] is False
    assert "image_too_dark" in body["quality_reason"]
    assert body["processed_frame"] is None


def test_a_device_without_a_bound_plant_is_404(
    client_as: ClientAs,
    add_plant: Callable[..., uuid.UUID],
    add_device: Callable[..., Device],
    storage: FakeStorage,
) -> None:
    add_plant("cam-1")
    unbound = add_device("cam-9")
    # Bound, but by an earlier owner: never lands on that owner's plant.
    passed_on = add_device("cam-2", owner_id=uuid.uuid4())
    add_plant("cam-2")

    assert _device_upload(client_as(unbound), plant_jpeg())[0] == 404
    assert _device_upload(client_as(passed_on), plant_jpeg())[0] == 404
    assert storage.objects == {}


def test_an_upload_needs_a_device_token(client_as: ClientAs, plant_id: uuid.UUID) -> None:
    assert _device_upload(client_as(None), plant_jpeg())[0] == 401


def test_unusable_uploads_are_422_or_413(
    client_as: ClientAs,
    plant_id: uuid.UUID,
    add_device: Callable[..., Device],
    storage: FakeStorage,
    monkeypatch,
) -> None:
    client = client_as(add_device("cam-1"))

    assert _device_upload(client, b"not a jpeg")[0] == 422
    assert _device_upload(client, plant_jpeg(), captured_at="2999-01-01T00:00:00Z")[0] == 422

    from capture import service

    monkeypatch.setattr(service, "MAX_UPLOAD_BYTES", 10)
    assert _device_upload(client, plant_jpeg())[0] == 413
    assert storage.objects == {}


def test_storage_failure_is_503(
    client_as: ClientAs,
    plant_id: uuid.UUID,
    add_device: Callable[..., Device],
    storage: FakeStorage,
) -> None:
    storage.down = True

    assert _device_upload(client_as(add_device("cam-1")), plant_jpeg())[0] == 503


# --------------------------------------------------------------------------- #
# admin routes
# --------------------------------------------------------------------------- #


def test_admin_routes_require_an_admin(
    client_as: ClientAs, owner: User, plant_id: uuid.UUID
) -> None:
    assert _admin_upload(client_as(None), plant_id, plant_jpeg())[0] == 401
    assert _admin_upload(client_as(owner), plant_id, plant_jpeg())[0] == 403
    assert client_as(owner).get(f"/capture/plants/{plant_id}/frames").status_code == 403


def test_an_admin_can_upload_for_a_plant(
    client_as: ClientAs, admin: User, plant_id: uuid.UUID, storage: FakeStorage
) -> None:
    code, body = _admin_upload(client_as(admin), plant_id, plant_jpeg())

    assert code == 201
    assert body["quality_passed"] is True


def test_admin_upload_for_an_unknown_or_archived_plant_is_404(
    client_as: ClientAs,
    admin: User,
    add_plant: Callable[..., uuid.UUID],
    storage: FakeStorage,
) -> None:
    archived = add_plant(archived=True)

    assert _admin_upload(client_as(admin), uuid.uuid4(), plant_jpeg())[0] == 404
    assert _admin_upload(client_as(admin), archived, plant_jpeg())[0] == 404


def test_frames_lists_newest_first(
    client_as: ClientAs, admin: User, plant_id: uuid.UUID, storage: FakeStorage
) -> None:
    client = client_as(admin)
    for _ in range(3):
        _admin_upload(client, plant_id, plant_jpeg())

    response = client.get(f"/capture/plants/{plant_id}/frames", params={"limit": 2})

    assert response.status_code == 200
    frames = response.json()
    assert len(frames) == 2
    assert frames[0]["processed_at"] >= frames[1]["processed_at"]
    assert client.get(f"/capture/plants/{uuid.uuid4()}/frames").status_code == 404
    assert client.get(f"/capture/plants/{plant_id}/frames", params={"limit": 51}).status_code == 422
