"""Integration and unit tests for GET /companion/devices/me/state API."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from action.models import CareEvent
from advice.models import CarePlan, CarePlanAction
from assessment.models import Observation, TriggerResultRecord
from companion.models import MessageRecord
from core.devices.models import Device
from core.users.models import User
from knowledge.models import Species
from registry.models import Plant


def test_unauthenticated_request_returns_401(client: TestClient) -> None:
    res = client.get("/companion/devices/me/state")
    assert res.status_code == 401
    body = res.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "UNAUTHORIZED"


def test_invalid_bearer_token_returns_401(client: TestClient) -> None:
    headers = {"Authorization": "Bearer invalid_garbage_token"}
    res = client.get("/companion/devices/me/state", headers=headers)
    assert res.status_code == 401
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == "UNAUTHORIZED"


def test_user_without_plant_id_returns_400(
    client: TestClient,
    add_user: Callable[..., User],
    user_token: Callable[[User], str],
) -> None:
    alice = add_user("alice@example.com")
    token = user_token(alice)
    headers = {"Authorization": f"Bearer {token}"}

    res = client.get("/companion/devices/me/state", headers=headers)
    assert res.status_code == 400
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == "PLANT_ID_REQUIRED"


def test_user_requesting_nonexistent_plant_returns_404(
    client: TestClient,
    add_user: Callable[..., User],
    user_token: Callable[[User], str],
) -> None:
    alice = add_user("alice@example.com")
    token = user_token(alice)
    headers = {"Authorization": f"Bearer {token}"}
    fake_id = uuid.uuid4()

    res = client.get(f"/companion/devices/me/state?plant_id={fake_id}", headers=headers)
    assert res.status_code == 404
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == "PLANT_NOT_FOUND"


def test_user_accessing_another_users_plant_returns_403(
    client: TestClient,
    add_user: Callable[..., User],
    add_plant: Callable[..., Plant],
    user_token: Callable[[User], str],
) -> None:
    alice = add_user("alice@example.com")
    bob = add_user("bob@example.com")
    bobs_plant = add_plant(bob, name="Bob's Cactus")

    alice_token = user_token(alice)
    headers = {"Authorization": f"Bearer {alice_token}"}

    res = client.get(
        f"/companion/devices/me/state?plant_id={bobs_plant.id}", headers=headers
    )
    assert res.status_code == 403
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == "FORBIDDEN"
    assert body["error"]["details"]["plant_id"] == str(bobs_plant.id)


def test_device_not_bound_returns_404(
    client: TestClient,
    add_user: Callable[..., User],
    add_device: Callable[..., tuple[Device, str]],
) -> None:
    alice = add_user("alice@example.com")
    _, raw_token = add_device(alice, physical_id="unbound_device_1")
    headers = {"Authorization": f"Bearer {raw_token}"}

    res = client.get("/companion/devices/me/state", headers=headers)
    assert res.status_code == 404
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == "DEVICE_NOT_BOUND"


def test_device_authenticated_happy_path_no_action(
    client: TestClient,
    session_factory: sessionmaker[Session],
    add_user: Callable[..., User],
    add_device: Callable[..., tuple[Device, str]],
    add_plant: Callable[..., Plant],
) -> None:
    alice = add_user("alice@example.com")
    device_serial = "planter_edge_001"
    _, raw_token = add_device(alice, physical_id=device_serial)
    plant = add_plant(
        alice,
        name="Monty",
        species_code="monstera_deliciosa",
        device_id=device_serial,
        level=3,
        xp_ratio=0.75,
    )

    # Seed species in knowledge
    with session_factory() as session:
        species = Species(
            species_code="monstera_deliciosa",
            scientific_name="Monstera deliciosa",
            common_name="Swiss Cheese Plant",
        )
        session.add(species)

        # Seed observation
        obs = Observation(
            id=uuid.uuid4(),
            plant_id=plant.id,
            run_id=uuid.uuid4(),
            timestamp=datetime.now(UTC),
            health_status="healthy",
            confidence=0.95,
            observations_json=[],
            image_refs_json=[],
        )
        session.add(obs)

        # Seed trigger result
        tr = TriggerResultRecord(
            id=uuid.uuid4(),
            run_id=obs.run_id,
            plant_id=plant.id,
            decision="NO_ACTION",
            confidence=0.95,
            reasoning="Plant displays optimal vigor and turgor.",
        )
        session.add(tr)

        # Seed companion message
        msg = MessageRecord(
            id=uuid.uuid4(),
            plant_id=plant.id,
            run_id=obs.run_id,
            decision="NO_ACTION",
            message="Soaking up the gentle morning light! Feeling great! ☀️",
            source="template_steady",
        )
        session.add(msg)
        session.commit()

    headers = {"Authorization": f"Bearer {raw_token}"}
    res = client.get("/companion/devices/me/state", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    data = body["data"]

    assert data["plant_id"] == str(plant.id)
    assert data["name"] == "Monty"
    assert data["species"] == "Monstera deliciosa"
    assert data["dayCount"] == 1
    assert data["level"] == 3
    assert data["xpRatio"] == 0.75
    assert data["health_status"] == "healthy"
    assert data["decision"] == "NO_ACTION"
    assert data["companion_message"] == "Soaking up the gentle morning light! Feeling great! ☀️"
    assert data["care_plan"] is None


def test_user_authenticated_happy_path_care_advice_required(
    client: TestClient,
    session_factory: sessionmaker[Session],
    add_user: Callable[..., User],
    add_plant: Callable[..., Plant],
    user_token: Callable[[User], str],
) -> None:
    alice = add_user("alice@example.com")
    plant = add_plant(
        alice,
        name="Basil",
        species_code="ocimum_basilicum",
        level=1,
        xp_ratio=0.2,
    )
    token = user_token(alice)
    headers = {"Authorization": f"Bearer {token}"}

    watered_time = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)

    with session_factory() as session:
        # Seed species
        session.add(
            Species(
                species_code="ocimum_basilicum",
                scientific_name="Ocimum basilicum",
                common_name="Sweet Basil",
            )
        )

        # Seed 3 observations for dayCount = 3
        run_id = uuid.uuid4()
        for i in range(3):
            session.add(
                Observation(
                    id=uuid.uuid4(),
                    plant_id=plant.id,
                    run_id=run_id if i == 2 else uuid.uuid4(),
                    timestamp=datetime.now(UTC),
                    health_status="possibly_unhealthy",
                    confidence=0.88,
                    observations_json=[],
                    image_refs_json=[],
                )
            )

        # Seed trigger result
        session.add(
            TriggerResultRecord(
                id=uuid.uuid4(),
                run_id=run_id,
                plant_id=plant.id,
                decision="CARE_ADVICE_REQUIRED",
                confidence=0.88,
                reasoning="Soil oversaturation detected.",
            )
        )

        # Seed care event (last watered)
        session.add(
            CareEvent(
                id=uuid.uuid4(),
                plant_id=plant.id,
                event_type="watered",
                occurred_at=watered_time,
                recorded_at=watered_time,
            )
        )

        # Seed care plan and normalized actions
        care_plan_pk = uuid.uuid4()
        care_plan_id = "cp_basil_water_01"
        plan = CarePlan(
            id=care_plan_pk,
            care_plan_id=care_plan_id,
            plant_id=plant.id,
            run_id=run_id,
            status_label="Waterlogged Roots",
            assessment="Excess moisture around root ball.",
            confidence=0.9,
        )
        session.add(plan)
        session.flush()

        action1 = CarePlanAction(
            id=uuid.uuid4(),
            care_plan_pk=care_plan_pk,
            care_plan_id=care_plan_id,
            action_id="act_pause_water",
            priority=1,
            action="Do not water until top 2 inches dry out.",
            label="Pause water",
            action_type="water",
        )
        action2 = CarePlanAction(
            id=uuid.uuid4(),
            care_plan_pk=care_plan_pk,
            care_plan_id=care_plan_id,
            action_id="act_check_drainage",
            priority=2,
            action="Inspect pot drainage holes for blockage.",
            label="Check holes",
            action_type="inspect",
        )
        session.add_all([action1, action2])

        # Seed dialogue
        session.add(
            MessageRecord(
                id=uuid.uuid4(),
                plant_id=plant.id,
                run_id=run_id,
                decision="CARE_ADVICE_REQUIRED",
                message="My roots feel submerged! Please help me dry out! 🥺",
                source="llm",
            )
        )
        session.commit()

    res = client.get(
        f"/companion/devices/me/state?plant_id={plant.id}", headers=headers
    )
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    data = body["data"]

    assert data["plant_id"] == str(plant.id)
    assert data["name"] == "Basil"
    assert data["species"] == "Ocimum basilicum"
    assert data["dayCount"] == 3
    assert data["wateredTimestamp"] is not None
    assert data["health_status"] == "possibly_unhealthy"
    assert data["decision"] == "CARE_ADVICE_REQUIRED"
    assert "submerged" in data["companion_message"]

    care_plan = data["care_plan"]
    assert care_plan is not None
    assert care_plan["id"] == "cp_basil_water_01"
    assert care_plan["status_label"] == "Waterlogged Roots"
    assert len(care_plan["actions"]) == 2
    assert care_plan["actions"][0]["id"] == "act_pause_water"
    assert care_plan["actions"][0]["label"] == "Pause water"
    assert care_plan["actions"][0]["type"] == "water"


def test_swagger_openapi_schema(client: TestClient) -> None:
    res = client.get("/openapi.json")
    assert res.status_code == 200
    spec = res.json()

    # Verify Companion tag is documented
    tag_names = [t["name"] for t in spec.get("tags", [])]
    assert "Companion" in tag_names

    # Verify endpoint is registered
    endpoint_doc = spec["paths"].get("/companion/devices/me/state", {}).get("get")
    assert endpoint_doc is not None
    assert endpoint_doc["summary"] == "Get current companion device state"
    assert "Companion" in endpoint_doc["tags"]
    assert "200" in endpoint_doc["responses"]
    assert "400" in endpoint_doc["responses"]
    assert "401" in endpoint_doc["responses"]
    assert "403" in endpoint_doc["responses"]
    assert "404" in endpoint_doc["responses"]
    assert "500" in endpoint_doc["responses"]
