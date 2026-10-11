"""Seed database with plants representing each LLM pipeline delivery scenario.

Creates test records in PostgreSQL for:
1. Steady / Healthy State (NO_ACTION)
2. Action Needed / LLM Diagnosis & Care Plan (CARE_ADVICE_REQUIRED)
3. Fallback Photo Retake (REQUEST_MORE_INFORMATION)

Run directly via:
    uv run python scripts/seed_companion_scenarios.py
"""

import sys
import uuid
from datetime import UTC, datetime, timedelta

# Ensure src/ is on sys.path
sys.path.insert(0, "src")

from action.models import CareEvent
from advice.models import CarePlan, CarePlanAction
from assessment.models import Observation, TriggerResultRecord
from companion.models import MessageRecord
from core.db import SessionLocal, init_models
from core.devices.models import Device
from core.security import hash_opaque_token, hash_password
from core.users.models import User
from knowledge.models import Species
from registry.models import Plant


def seed_scenarios() -> dict[str, str]:
    """Insert seeded scenarios into database and return plant IDs."""
    init_models()

    session = SessionLocal()
    try:
        # 1. Create or get test user
        email = "postman_tester@example.com"
        user = session.query(User).filter(User.email == email).first()
        if not user:
            user = User(
                id=uuid.uuid4(),
                email=email,
                name="Postman Tester",
                hashed_password=hash_password("Password123!"),
                is_active=True,
                is_superuser=False,
            )
            session.add(user)
            session.flush()

        # 2. Ensure species catalogue has supported test species
        for code, sci_name, com_name in [
            ("spath", "Spathiphyllum wallisii", "Peace Lily"),
            ("monstera_deliciosa", "Monstera deliciosa", "Swiss Cheese Plant"),
            ("ocimum_basilicum", "Ocimum basilicum", "Sweet Basil"),
        ]:
            if not session.query(Species).filter(Species.species_code == code).first():
                session.add(
                    Species(
                        species_code=code,
                        scientific_name=sci_name,
                        common_name=com_name,
                    )
                )
        session.flush()

        now = datetime.now(UTC)

        # -------------------------------------------------------------
        # SCENARIO 1: Steady / Healthy State (NO_ACTION)
        # -------------------------------------------------------------
        steady_device_phys_id = "dev_steady_001"
        steady_device_token = "ppd_steady_device_token_secret_123"
        steady_device = (
            session.query(Device)
            .filter(Device.physical_id == steady_device_phys_id)
            .first()
        )
        if not steady_device:
            steady_device = Device(
                id=uuid.uuid4(),
                physical_id=steady_device_phys_id,
                owner_id=user.id,
                name="Steady Living Room Device",
                status="active",
                token_hash=hash_opaque_token(steady_device_token),
            )
            session.add(steady_device)
            session.flush()

        steady_plant = (
            session.query(Plant)
            .filter(Plant.owner_id == user.id, Plant.name == "Steady Monty (NO_ACTION)")
            .first()
        )
        if not steady_plant:
            steady_plant = Plant(
                id=uuid.uuid4(),
                owner_id=user.id,
                name="Steady Monty (NO_ACTION)",
                species_code="monstera_deliciosa",
                device_id=steady_device_phys_id,
                level=3,
                xp_ratio=0.85,
            )
            session.add(steady_plant)
            session.flush()
        else:
            steady_plant.device_id = steady_device_phys_id

        run_id_steady = uuid.uuid4()
        session.add(
            Observation(
                id=uuid.uuid4(),
                plant_id=steady_plant.id,
                run_id=run_id_steady,
                timestamp=now - timedelta(hours=2),
                health_status="healthy",
                confidence=0.96,
                observations_json=[],
                image_refs_json=["obs_steady_01.jpg"],
                description="Vibrant foliage with optimal turgor pressure.",
            )
        )
        session.add(
            TriggerResultRecord(
                id=uuid.uuid4(),
                run_id=run_id_steady,
                plant_id=steady_plant.id,
                decision="NO_ACTION",
                confidence=0.96,
                reasoning="Deterministic rule check: all vitals within nominal green range.",
            )
        )
        session.add(
            MessageRecord(
                id=uuid.uuid4(),
                plant_id=steady_plant.id,
                run_id=run_id_steady,
                decision="NO_ACTION",
                message="Soaking up the gentle morning light! Feeling great! ☀️",
                source="template_steady",
            )
        )

        # -------------------------------------------------------------
        # SCENARIO 2: Action Needed (CARE_ADVICE_REQUIRED)
        # -------------------------------------------------------------
        care_device_phys_id = "dev_care_002"
        care_device_token = "ppd_care_device_token_secret_456"
        care_device = (
            session.query(Device)
            .filter(Device.physical_id == care_device_phys_id)
            .first()
        )
        if not care_device:
            care_device = Device(
                id=uuid.uuid4(),
                physical_id=care_device_phys_id,
                owner_id=user.id,
                name="Care Advice Herb Planter",
                status="active",
                token_hash=hash_opaque_token(care_device_token),
            )
            session.add(care_device)
            session.flush()

        care_plant = (
            session.query(Plant)
            .filter(
                Plant.owner_id == user.id,
                Plant.name == "Stressed Basil (CARE_ADVICE_REQUIRED)",
            )
            .first()
        )
        if not care_plant:
            care_plant = Plant(
                id=uuid.uuid4(),
                owner_id=user.id,
                name="Stressed Basil (CARE_ADVICE_REQUIRED)",
                species_code="ocimum_basilicum",
                device_id=care_device_phys_id,
                level=1,
                xp_ratio=0.30,
            )
            session.add(care_plant)
            session.flush()
        else:
            care_plant.device_id = care_device_phys_id

        run_id_care = uuid.uuid4()
        session.add(
            Observation(
                id=uuid.uuid4(),
                plant_id=care_plant.id,
                run_id=run_id_care,
                timestamp=now - timedelta(hours=1),
                health_status="possibly_unhealthy",
                confidence=0.89,
                observations_json=[{"symptom": "soil_saturation", "severity": 0.82}],
                image_refs_json=["obs_saturated_01.jpg"],
                description="Soil waterlogged with early drooping of lower leaves.",
            )
        )
        session.add(
            TriggerResultRecord(
                id=uuid.uuid4(),
                run_id=run_id_care,
                plant_id=care_plant.id,
                decision="CARE_ADVICE_REQUIRED",
                primary_symptom="overwatering_stress",
                confidence=0.89,
                reasoning="Soil moisture high across consecutive scans with drooping.",
            )
        )
        session.add(
            CareEvent(
                id=uuid.uuid4(),
                plant_id=care_plant.id,
                event_type="watered",
                occurred_at=now - timedelta(hours=36),
                recorded_at=now - timedelta(hours=36),
            )
        )

        care_plan_pk = uuid.uuid4()
        care_plan_id = "cp_basil_overwater_001"
        session.add(
            CarePlan(
                id=care_plan_pk,
                care_plan_id=care_plan_id,
                plant_id=care_plant.id,
                run_id=run_id_care,
                status_label="Overwatering Stress",
                assessment="Excess moisture in root zone causing hypoxia. Root dry-down required.",
                confidence=0.92,
            )
        )
        session.flush()

        session.add_all(
            [
                CarePlanAction(
                    id=uuid.uuid4(),
                    care_plan_pk=care_plan_pk,
                    care_plan_id=care_plan_id,
                    action_id="act_pause_water",
                    priority=1,
                    action="Pause all watering until top 5cm soil is completely dry.",
                    label="Pause water",
                    action_type="water",
                ),
                CarePlanAction(
                    id=uuid.uuid4(),
                    care_plan_pk=care_plan_pk,
                    care_plan_id=care_plan_id,
                    action_id="act_check_drainage",
                    priority=2,
                    action="Empty excess saucer water and inspect pot drainage holes.",
                    label="Check drainage",
                    action_type="inspect",
                ),
            ]
        )

        session.add(
            MessageRecord(
                id=uuid.uuid4(),
                plant_id=care_plant.id,
                run_id=run_id_care,
                decision="CARE_ADVICE_REQUIRED",
                message="Whew, my roots feel waterlogged! Could we hold off watering for a bit? 🌱",
                source="llm",
            )
        )

        # -------------------------------------------------------------
        # SCENARIO 3: Photo Retake (REQUEST_MORE_INFORMATION)
        # -------------------------------------------------------------
        retake_device_phys_id = "dev_retake_003"
        retake_device_token = "ppd_retake_device_token_secret_789"
        retake_device = (
            session.query(Device)
            .filter(Device.physical_id == retake_device_phys_id)
            .first()
        )
        if not retake_device:
            retake_device = Device(
                id=uuid.uuid4(),
                physical_id=retake_device_phys_id,
                owner_id=user.id,
                name="Retake Photo Camera Device",
                status="active",
                token_hash=hash_opaque_token(retake_device_token),
            )
            session.add(retake_device)
            session.flush()

        retake_plant = (
            session.query(Plant)
            .filter(
                Plant.owner_id == user.id,
                Plant.name == "Blurry Fern (REQUEST_MORE_INFORMATION)",
            )
            .first()
        )
        if not retake_plant:
            retake_plant = Plant(
                id=uuid.uuid4(),
                owner_id=user.id,
                name="Blurry Fern (REQUEST_MORE_INFORMATION)",
                species_code="spath",
                device_id=retake_device_phys_id,
                level=2,
                xp_ratio=0.50,
            )
            session.add(retake_plant)
            session.flush()
        else:
            retake_plant.device_id = retake_device_phys_id

        run_id_retake = uuid.uuid4()
        session.add(
            Observation(
                id=uuid.uuid4(),
                plant_id=retake_plant.id,
                run_id=run_id_retake,
                timestamp=now - timedelta(minutes=30),
                health_status="possibly_unhealthy",
                confidence=0.42,
                observations_json=[],
                consensus_json={"agreement": 0.42, "reason": "motion_blur"},
                image_refs_json=["obs_blur_01.jpg"],
                description="Low light and motion blur prevented high-confidence analysis.",
            )
        )
        session.add(
            TriggerResultRecord(
                id=uuid.uuid4(),
                run_id=run_id_retake,
                plant_id=retake_plant.id,
                decision="REQUEST_MORE_INFORMATION",
                confidence=0.42,
                reasoning="Confidence threshold (< 0.50) unmet due to motion blur.",
            )
        )
        session.add(
            MessageRecord(
                id=uuid.uuid4(),
                plant_id=retake_plant.id,
                run_id=run_id_retake,
                decision="REQUEST_MORE_INFORMATION",
                message=(
                    "Hmm, that photo was a bit blurry or dim. "
                    "Could you snap another one with good lighting? 📸"
                ),
                source="template_info_request",
            )
        )

        session.commit()

        results = {
            "user_email": email,
            "user_password": "Password123!",
            "steady_plant_id": str(steady_plant.id),
            "steady_device_token": steady_device_token,
            "care_advice_plant_id": str(care_plant.id),
            "care_advice_device_token": care_device_token,
            "photo_retake_plant_id": str(retake_plant.id),
            "photo_retake_device_token": retake_device_token,
        }

        print("=" * 65)
        print("🌱 SEEDED COMPANION PIPELINE SCENARIOS SUCCESSFULLY")
        print("=" * 65)
        print(f"User:                   {results['user_email']}")
        print(f"Password:               {results['user_password']}")
        print(f"1. Steady (NO_ACTION):  Plant ID: {results['steady_plant_id']}")
        print(f"                        Device Token: {results['steady_device_token']}")
        print(f"2. Care Advice (LLM):   Plant ID: {results['care_advice_plant_id']}")
        print(f"                        Device Token: {results['care_advice_device_token']}")
        print(f"3. Retake Photo:        Plant ID: {results['photo_retake_plant_id']}")
        print(f"                        Device Token: {results['photo_retake_device_token']}")
        print("=" * 65)

        return results

    finally:
        session.close()


def clean_scenarios() -> None:
    """Remove all seeded test data for postman_tester from the database."""
    init_models()
    session = SessionLocal()
    try:
        email = "postman_tester@example.com"
        user = session.query(User).filter(User.email == email).first()
        if not user:
            print(f"No test records found for {email}. Database is already clean.")
            return

        plants = session.query(Plant).filter(Plant.owner_id == user.id).all()
        plant_ids = [p.id for p in plants]

        if plant_ids:
            care_plans = (
                session.query(CarePlan).filter(CarePlan.plant_id.in_(plant_ids)).all()
            )
            care_plan_pks = [cp.id for cp in care_plans]
            if care_plan_pks:
                session.query(CarePlanAction).filter(
                    CarePlanAction.care_plan_pk.in_(care_plan_pks)
                ).delete(synchronize_session=False)

            session.query(CarePlan).filter(CarePlan.plant_id.in_(plant_ids)).delete(
                synchronize_session=False
            )
            session.query(CareEvent).filter(CareEvent.plant_id.in_(plant_ids)).delete(
                synchronize_session=False
            )
            session.query(Observation).filter(Observation.plant_id.in_(plant_ids)).delete(
                synchronize_session=False
            )
            session.query(TriggerResultRecord).filter(
                TriggerResultRecord.plant_id.in_(plant_ids)
            ).delete(synchronize_session=False)
            session.query(MessageRecord).filter(
                MessageRecord.plant_id.in_(plant_ids)
            ).delete(synchronize_session=False)

            session.query(Plant).filter(Plant.owner_id == user.id).delete(
                synchronize_session=False
            )

        test_device_ids = ["dev_steady_001", "dev_care_002", "dev_retake_003"]
        session.query(Device).filter(
            (Device.owner_id == user.id) | (Device.physical_id.in_(test_device_ids))
        ).delete(synchronize_session=False)

        session.delete(user)
        session.commit()

        print("=" * 65)
        print("🧹 CLEANED UP ALL TEST ARTIFACTS SUCCESSFULLY")
        print(f"Removed user: {email} and all linked plants, devices, & plans.")
        print("Database returned to pristine state.")
        print("=" * 65)
    finally:
        session.close()


if __name__ == "__main__":
    if "--clean" in sys.argv:
        clean_scenarios()
    else:
        seed_scenarios()
