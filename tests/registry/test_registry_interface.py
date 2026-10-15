"""Published in-process interface: ``registry.interface``."""

import json
import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from core.devices.models import Device
from core.users.models import User
from registry import (
    get_plant,
    get_plant_by_device,
    is_plant_owned_by,
    list_plant_ids,
    service,
)
from registry.schemas import PlantCreate


def _register(
    session: Session, owner: User, name: str, **fields: str | None
) -> uuid.UUID:
    return service.create_plant(
        session, PlantCreate(name=name, **fields), owner_id=owner.id  # type: ignore[arg-type]
    ).id


def test_list_plant_ids_and_filters(
    session_factory: sessionmaker[Session],
    add_species: Callable[..., None],
    alice: User,
    bob: User,
) -> None:
    add_species("spath", "pothos")
    with session_factory() as session:
        a1 = _register(session, alice, "a1", species_code="spath")
        a2 = _register(session, alice, "a2", species_code="pothos")
        b1 = _register(session, bob, "b1", species_code="spath")

        assert list_plant_ids(session) == [a1, a2, b1]
        assert list_plant_ids(session, owner_id=alice.id) == [a1, a2]
        assert list_plant_ids(session, species_code="spath") == [a1, b1]

        service.archive_plant(session, service.get_plant(session, a2))  # type: ignore[arg-type]
        assert list_plant_ids(session, owner_id=alice.id) == [a1]
        assert list_plant_ids(session, owner_id=alice.id, include_archived=True) == [a1, a2]


def test_get_plant_is_json_serialisable(
    session_factory: sessionmaker[Session], add_device: Callable[..., Device], alice: User
) -> None:
    add_device(alice, "dev-1")
    with session_factory() as session:
        plant_id = _register(session, alice, "a", device_id="dev-1")
        plant = get_plant(session, plant_id)

        assert plant is not None
        assert plant.owner_id == alice.id
        assert plant.is_active
        payload = plant.model_dump(mode="json")
        assert json.loads(json.dumps(payload))["device_id"] == "dev-1"

        assert get_plant(session, uuid.uuid4()) is None


def test_archived_plant_stays_resolvable(
    session_factory: sessionmaker[Session], add_device: Callable[..., Device], alice: User
) -> None:
    add_device(alice, "dev-1")
    with session_factory() as session:
        plant_id = _register(session, alice, "a", device_id="dev-1")
        service.archive_plant(session, service.get_plant(session, plant_id))  # type: ignore[arg-type]

        plant = get_plant(session, plant_id)
        assert plant is not None and not plant.is_active
        # …but the device no longer resolves to it
        assert get_plant_by_device(session, "dev-1") is None


def test_get_plant_by_device(
    session_factory: sessionmaker[Session], add_device: Callable[..., Device], alice: User
) -> None:
    add_device(alice, "dev-1")
    with session_factory() as session:
        plant_id = _register(session, alice, "a", device_id="dev-1")
        plant = get_plant_by_device(session, "dev-1")
        assert plant is not None and plant.id == plant_id
        assert get_plant_by_device(session, "dev-2") is None


def test_is_plant_owned_by(
    session_factory: sessionmaker[Session], alice: User, bob: User
) -> None:
    with session_factory() as session:
        plant_id = _register(session, alice, "a")
        assert is_plant_owned_by(session, plant_id, alice.id)
        assert not is_plant_owned_by(session, plant_id, bob.id)
        assert not is_plant_owned_by(session, uuid.uuid4(), alice.id)
