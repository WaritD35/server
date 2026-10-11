"""``/registry`` HTTP API tests."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from core.devices.models import Device
from core.users.models import User

ClientAs = Callable[[User | Device], TestClient]
MISSING = "00000000-0000-0000-0000-000000000000"


@pytest.fixture(autouse=True)
def _species(add_species: Callable[..., None]) -> None:
    add_species("spath", "pothos")


@pytest.fixture
def alice_device(add_device: Callable[..., Device], alice: User) -> Device:
    return add_device(alice, "dev-1")


def test_crud_round_trip(client_as: ClientAs, alice: User, alice_device: Device) -> None:
    client = client_as(alice)
    created = client.post(
        "/registry/plants",
        json={"name": "Spathi", "species_code": "spath", "device_id": "dev-1"},
    )
    assert created.status_code == 201
    plant = created.json()
    assert plant["owner_id"] == str(alice.id)
    assert plant["species_confirmed_at"] is None
    assert plant["archived_at"] is None
    plant_id = plant["id"]

    assert client.get(f"/registry/plants/{plant_id}").json()["name"] == "Spathi"
    assert [p["id"] for p in client.get("/registry/plants").json()] == [plant_id]

    patched = client.patch(
        f"/registry/plants/{plant_id}", json={"name": "Spathi II", "note": "kitchen"}
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Spathi II"
    assert patched.json()["note"] == "kitchen"
    assert patched.json()["device_id"] == "dev-1"  # untouched

    assert client.delete(f"/registry/plants/{plant_id}").status_code == 204
    # archived: gone from the default listing, still resolvable by id
    assert client.get("/registry/plants").json() == []
    archived = client.get("/registry/plants", params={"include_archived": True}).json()
    assert [p["id"] for p in archived] == [plant_id]
    assert client.get(f"/registry/plants/{plant_id}").json()["archived_at"] is not None

    # an archived plant cannot be changed or archived again
    assert client.patch(f"/registry/plants/{plant_id}", json={"name": "x"}).status_code == 409
    assert client.delete(f"/registry/plants/{plant_id}").status_code == 409


def test_requires_authentication(client_as: ClientAs, alice: User) -> None:
    client = client_as(alice)
    client.app.dependency_overrides.clear()  # type: ignore[attr-defined]
    assert client.get("/registry/plants").status_code == 401


def test_owners_cannot_see_each_others_plants(
    client_as: ClientAs, alice: User, bob: User
) -> None:
    plant_id = client_as(alice).post("/registry/plants", json={"name": "a"}).json()["id"]

    as_bob = client_as(bob)
    assert as_bob.get("/registry/plants").json() == []
    # 404, not 403: another owner's id is indistinguishable from a missing one
    assert as_bob.get(f"/registry/plants/{plant_id}").status_code == 404
    assert as_bob.patch(f"/registry/plants/{plant_id}", json={"name": "b"}).status_code == 404
    assert as_bob.delete(f"/registry/plants/{plant_id}").status_code == 404
    # an owner's owner_id filter cannot widen the listing either
    assert as_bob.get("/registry/plants", params={"owner_id": str(alice.id)}).json() == []


def test_admin_sees_everything_and_registers_for_others(
    client_as: ClientAs, alice: User, bob: User, admin: User
) -> None:
    client_as(alice).post("/registry/plants", json={"name": "a"})
    client_as(bob).post("/registry/plants", json={"name": "b"})

    as_admin = client_as(admin)
    assert {p["name"] for p in as_admin.get("/registry/plants").json()} == {"a", "b"}
    only_bob = as_admin.get("/registry/plants", params={"owner_id": str(bob.id)}).json()
    assert [p["name"] for p in only_bob] == ["b"]

    created = as_admin.post("/registry/plants", json={"name": "c", "owner_id": str(alice.id)})
    assert created.status_code == 201
    assert created.json()["owner_id"] == str(alice.id)

    unknown = as_admin.post("/registry/plants", json={"name": "d", "owner_id": MISSING})
    assert unknown.status_code == 422


def test_owner_cannot_register_for_someone_else(
    client_as: ClientAs, alice: User, bob: User
) -> None:
    response = client_as(alice).post(
        "/registry/plants", json={"name": "a", "owner_id": str(bob.id)}
    )
    assert response.status_code == 403


def test_species_must_exist_in_knowledge(client_as: ClientAs, alice: User) -> None:
    client = client_as(alice)
    assert client.post(
        "/registry/plants", json={"name": "a", "species_code": "nope"}
    ).status_code == 422

    plant_id = client.post("/registry/plants", json={"name": "a"}).json()["id"]
    assert client.patch(
        f"/registry/plants/{plant_id}", json={"species_code": "nope"}
    ).status_code == 422


def test_species_confirmation(client_as: ClientAs, alice: User) -> None:
    client = client_as(alice)
    plant = client.post(
        "/registry/plants", json={"name": "a", "species_code": "spath"}
    ).json()
    url = f"/registry/plants/{plant['id']}"
    assert plant["species_confirmed_at"] is None

    confirmed = client.patch(url, json={"species_confirmed": True}).json()
    assert confirmed["species_confirmed_at"] is not None

    # changing the species drops the confirmation…
    changed = client.patch(url, json={"species_code": "pothos"}).json()
    assert changed["species_code"] == "pothos"
    assert changed["species_confirmed_at"] is None

    # …unless the same request confirms the new one
    both = client.patch(url, json={"species_code": "spath", "species_confirmed": True}).json()
    assert both["species_confirmed_at"] is not None


def test_device_is_bound_to_one_live_plant(
    client_as: ClientAs, alice: User, alice_device: Device
) -> None:
    client = client_as(alice)
    first = client.post("/registry/plants", json={"name": "a", "device_id": "dev-1"}).json()

    dup = client.post("/registry/plants", json={"name": "b", "device_id": "dev-1"})
    assert dup.status_code == 409

    second = client.post("/registry/plants", json={"name": "b"}).json()
    assert client.patch(
        f"/registry/plants/{second['id']}", json={"device_id": "dev-1"}
    ).status_code == 409
    # re-sending a plant's own device is not a conflict
    assert client.patch(
        f"/registry/plants/{first['id']}", json={"device_id": "dev-1"}
    ).status_code == 200

    # archiving frees the device for a new plant
    client.delete(f"/registry/plants/{first['id']}")
    rebound = client.patch(f"/registry/plants/{second['id']}", json={"device_id": "dev-1"})
    assert rebound.status_code == 200


def test_unbind_device_with_null(
    client_as: ClientAs, alice: User, alice_device: Device
) -> None:
    client = client_as(alice)
    plant = client.post("/registry/plants", json={"name": "a", "device_id": "dev-1"}).json()
    unbound = client.patch(f"/registry/plants/{plant['id']}", json={"device_id": None})
    assert unbound.json()["device_id"] is None
    assert client.get("/registry/devices/dev-1/plant").status_code == 404


def test_device_lookup(
    client_as: ClientAs, alice: User, bob: User, admin: User, alice_device: Device
) -> None:
    plant = client_as(alice).post(
        "/registry/plants", json={"name": "a", "device_id": "dev-1"}
    ).json()

    assert client_as(alice).get("/registry/devices/dev-1/plant").json()["id"] == plant["id"]
    assert client_as(admin).get("/registry/devices/dev-1/plant").json()["id"] == plant["id"]
    assert client_as(bob).get("/registry/devices/dev-1/plant").status_code == 404
    assert client_as(alice).get("/registry/devices/dev-x/plant").status_code == 404


def test_missing_plant_returns_404(client_as: ClientAs, admin: User) -> None:
    client = client_as(admin)
    assert client.get(f"/registry/plants/{MISSING}").status_code == 404
    assert client.patch(f"/registry/plants/{MISSING}", json={"name": "x"}).status_code == 404
    assert client.delete(f"/registry/plants/{MISSING}").status_code == 404


def test_only_devices_paired_to_the_owner_can_be_bound(
    client_as: ClientAs, add_device: Callable[..., Device], alice: User, bob: User
) -> None:
    add_device(bob, "dev-bob")
    client = client_as(alice)
    # never paired
    assert client.post(
        "/registry/plants", json={"name": "a", "device_id": "dev-x"}
    ).status_code == 422
    # paired, but to someone else
    assert client.post(
        "/registry/plants", json={"name": "a", "device_id": "dev-bob"}
    ).status_code == 422
    plant = client.post("/registry/plants", json={"name": "a"}).json()
    assert client.patch(
        f"/registry/plants/{plant['id']}", json={"device_id": "dev-bob"}
    ).status_code == 422


def test_admin_binds_the_owners_device(
    client_as: ClientAs, alice: User, admin: User, alice_device: Device
) -> None:
    created = client_as(admin).post(
        "/registry/plants",
        json={"name": "a", "owner_id": str(alice.id), "device_id": "dev-1"},
    )
    assert created.status_code == 201
    # the admin's own account does not own dev-1, so this is refused
    assert client_as(admin).post(
        "/registry/plants", json={"name": "b", "device_id": "dev-1"}
    ).status_code == 422


def test_device_reads_its_own_plant(
    client_as: ClientAs, alice: User, alice_device: Device
) -> None:
    assert client_as(alice_device).get("/registry/devices/me/plant").status_code == 404

    plant = client_as(alice).post(
        "/registry/plants", json={"name": "a", "device_id": "dev-1"}
    ).json()
    mine = client_as(alice_device).get("/registry/devices/me/plant")
    assert mine.status_code == 200
    assert mine.json()["id"] == plant["id"]


def test_device_passed_on_to_a_new_owner(
    client_as: ClientAs,
    session_factory: sessionmaker[Session],
    alice: User,
    bob: User,
    alice_device: Device,
) -> None:
    alices = client_as(alice).post(
        "/registry/plants", json={"name": "a", "device_id": "dev-1"}
    ).json()

    # alice revokes, bob pairs it: the device row now belongs to bob
    with session_factory() as session:
        device = session.get(Device, alice_device.id)
        assert device is not None
        device.owner_id = bob.id
        session.commit()

    # the old binding no longer resolves — bob's frames must not land on alice's plant
    assert client_as(alice).get("/registry/devices/dev-1/plant").status_code == 404
    assert client_as(bob).get("/registry/devices/dev-1/plant").status_code == 404

    # bob can bind it; alice's stale binding is cleared rather than a 409
    bobs = client_as(bob).post("/registry/plants", json={"name": "b", "device_id": "dev-1"})
    assert bobs.status_code == 201
    assert client_as(bob).get("/registry/devices/dev-1/plant").json()["id"] == bobs.json()["id"]
    assert client_as(alice).get(f"/registry/plants/{alices['id']}").json()["device_id"] is None


def test_list_plants_pagination(client_as: ClientAs, alice: User) -> None:
    client = client_as(alice)
    for i in range(5):
        client.post("/registry/plants", json={"name": f"plant-{i}"})

    page1 = client.get("/registry/plants", params={"limit": 2, "offset": 0}).json()
    assert len(page1) == 2
    assert page1[0]["name"] == "plant-0"
    assert page1[1]["name"] == "plant-1"

    page2 = client.get("/registry/plants", params={"limit": 2, "offset": 2}).json()
    assert len(page2) == 2
    assert page2[0]["name"] == "plant-2"
    assert page2[1]["name"] == "plant-3"

    page3 = client.get("/registry/plants", params={"limit": 2, "offset": 4}).json()
    assert len(page3) == 1
    assert page3[0]["name"] == "plant-4"

