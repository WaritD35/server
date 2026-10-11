# Operational & Verification Scripts (`scripts/`)

This directory contains developer tooling and verification scripts for local development, pipeline sanity checking, and scenario data seeding.

---

## 1. Quick Reference: When to Use Which Script

| Script / Tool | Target Audience | What It Does | When to Use | Command |
| :--- | :--- | :--- | :--- | :--- |
| **`seed_companion_scenarios.py`** | 👤 **Human** & 🤖 **Agent** | Seeds PostgreSQL with 3 distinct LLM delivery modes (`NO_ACTION`, `CARE_ADVICE_REQUIRED`, `REQUEST_MORE_INFORMATION`), test user, and paired devices. | **Before manual or Postman API testing** of `GET /companion/devices/me/state` to populate realistic state data. | `uv run python scripts/seed_companion_scenarios.py` |
| **`seed_companion_scenarios.py --clean`** | 👤 **Human** & 🤖 **Agent** | Removes all seeded test users, devices, observations, care plans, and messages. | **Immediately after finishing API testing** to leave zero test artifacts/garbage in PostgreSQL. | `uv run python scripts/seed_companion_scenarios.py --clean` |
| **`verify_pipeline.py`** | 🤖 **Agent** & 👤 **Human** | Runs a self-contained in-memory SQLite pipeline run through the Orchestrator across all 4 stages (`capture` → `assessment` → `advice` → `companion`). | **When verifying pipeline orchestration wiring** without needing edge cameras, running Docker, or live LLM calls. | `uv run python scripts/verify_pipeline.py` |
| **`tests/mlops/test_scenarios_mock.py`** | 🤖 **Agent** (CI/CD) | Full pytest suite running all 6 plant health scenarios using deterministic mock LLM responses. | **During standard automated testing (`uv run pytest`)** or CI/CD to prevent regressions. | `uv run pytest tests/mlops/test_scenarios_mock.py` |
| **Postman Collection** (`tests/postman/`) | 👤 **Human** | End-to-end black-box HTTP regression suite testing auth, device pairing, error envelopes, and all 3 delivery scenarios. | **When verifying the live web API server** (`http://localhost:8000`) interactively from the Postman desktop app. | Run in Postman or via Newman CLI |

---

## 2. Detailed Script Documentation

### `scripts/seed_companion_scenarios.py`

#### Purpose
Populates your local PostgreSQL database with three real-world plant health states matching the 3 LLM delivery scenarios for the companion client interface:
1. **Steady / Healthy State (`NO_ACTION`)**:
   - Plant: `Steady Monty (NO_ACTION)` (`Monstera deliciosa`, Level 3, XP 85%)
   - Health: `healthy`, Confidence 0.96
   - Decision: `NO_ACTION`, `care_plan: null`
   - Companion Message: Positive morning light greeting.
   - Pre-shared Device Token: `ppd_steady_device_token_secret_123`
2. **Action Needed (`CARE_ADVICE_REQUIRED`)**:
   - Plant: `Stressed Basil (CARE_ADVICE_REQUIRED)` (`Ocimum basilicum`, Level 1, XP 30%)
   - Health: `possibly_unhealthy`, Primary Symptom: `overwatering_stress`
   - Decision: `CARE_ADVICE_REQUIRED`
   - Care Plan: `cp_basil_overwater_001` ("Overwatering Stress") with 2 actions (`Pause water`, `Check drainage`)
   - Companion Message: 1st-person plant voice asking to pause watering.
   - Pre-shared Device Token: `ppd_care_device_token_secret_456`
3. **Fallback Photo Retake (`REQUEST_MORE_INFORMATION`)**:
   - Plant: `Blurry Fern (REQUEST_MORE_INFORMATION)` (`Spathiphyllum wallisii`, Level 2, XP 50%)
   - Health: `possibly_unhealthy`, Confidence 0.42 (< 0.50 threshold due to motion blur)
   - Decision: `REQUEST_MORE_INFORMATION`, `care_plan: null`
   - Companion Message: Friendly prompt asking the user to snap a clearer photo.
   - Pre-shared Device Token: `ppd_retake_device_token_secret_789`

#### How to Use
```bash
# 1. Seed database:
uv run python scripts/seed_companion_scenarios.py

# 2. Test endpoints using edge device bearer tokens:
curl -s -H "Authorization: Bearer ppd_steady_device_token_secret_123" http://localhost:8000/companion/devices/me/state | jq .
curl -s -H "Authorization: Bearer ppd_care_device_token_secret_456" http://localhost:8000/companion/devices/me/state | jq .
curl -s -H "Authorization: Bearer ppd_retake_device_token_secret_789" http://localhost:8000/companion/devices/me/state | jq .

# 3. Test endpoints using mobile user JWT:
TOKEN=$(curl -s -X POST http://localhost:8000/auth/token -d "username=postman_tester@example.com&password=Password123!" | jq -r .access_token)
curl -s -H "Authorization: Bearer $TOKEN" "http://localhost:8000/companion/devices/me/state?plant_id=<plant_id>" | jq .

# 4. Clean up all seeded test data when finished:
uv run python scripts/seed_companion_scenarios.py --clean
```

---

### `scripts/verify_pipeline.py`

#### Purpose
Performs an end-to-end sanity check of the four pipeline stages orchestrated by `orchestrator.stages.STAGES`:
1. `capture`: Validates camera observation inputs and metadata.
2. `assessment`: Evaluates deterministic visual rules (0 LLM tokens) and triggers.
3. `advice`: Generates diagnostic reasoning and 3NF care plan actions.
4. `companion`: Synthesizes 1st-person character dialogue and validates fact preservation.

#### How to Use
```bash
uv run python scripts/verify_pipeline.py
```
Expected output:
```text
[Step 1] Executing Orchestrator Pipeline across all 4 stages...
  ✓ Pipeline Run Status: completed
  ✓ Final Stage Reached: companion
[Step 2] Validating Assessment Stage (0 LLM Tokens):
  ✓ Decision: CARE_ADVICE_REQUIRED
[Step 3] Validating Advice Stage (3NF Care Plan):
  ✓ Status Label: Overwatering Stress
  ✓ Generated 2 Actions
[Step 4] Validating Companion Stage (Character Message):
  ✓ Message: "..."
  ✓ Fact-preservation check: PASSED
```

