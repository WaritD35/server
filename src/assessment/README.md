# 4. Assessment

**Package:** `src/assessment/`
**Role:** Internal-state inference — run the VLM probe on a frame pair, persist
one observation, then apply the 0-token Event Engine.
**Owner:** _TBD_

## Responsibilities

- `assess_frames(session, plant_id, run_id, *, frames_run_id)` — compare two
  processed frames via `mlops.assessment.run_probe` and write one
  `assessment.observation`. Does **not** run the rules engine.
- Skip (raise `AssessmentSkipped`) when the plant is unknown, `species_code` is
  unset (`"species not set"`), or `capture.interface.get_frame_pair` returns
  `None` (`"baseline frame stored; need two frames"`).
- Resolve the species name with `knowledge.interface.get_species` (falls back to
  `species_code` with a warning; tracing only).
- Ask the species' **approved checks** (`knowledge.get_approved_checks`) in the
  same VLM prompt: screening checks first, then by `priority`, at most
  `MAX_PROBE_CHECKS` (8). A stale set is still used, with a warning. Only the
  structured checks are sent; `get_care_knowledge` and research text are not.
- Each VLM JSON answer is checked against `RunObservation`'s JSON Schema, then
  as that model. A failure is repaired once; a run that stays invalid is dropped
  and does not join the vote.
- Map the `ProbeVerdict` onto the observation: `health_status` ← verdict,
  `confidence` ← `consensus.agreement`, `observations_json` ← verdict
  observations (`type`, `severity`, `description`), `consensus_json` ←
  `{agreement, runs, model_stated_average}`, `image_refs_json` ←
  `s3://{frames_derived bucket}/{key}` for previous then current,
  `timestamp` ← the current capture's `captured_at`, `probe_set_id` and
  `probe_results_json` ← each check's voted answer plus its `care_need` and
  `question` (both `NULL` when no check was asked).
- `evaluate_observation` — 0-token Event Engine + milestone detection on the
  stored observation (orchestrator runs this after `assess_frames`). A check
  that fires and did not fire on the previous observation is a
  `CARE_ADVICE_REQUIRED` trigger with the check's slug as `primary_symptom`.

## Data it owns

- PostgreSQL schema `assessment`: `observation` (one VLM verdict per run),
  `trigger_result`, `plant_milestone`.
- MinIO: none (reads derived frames via `capture`).

## Inputs

- **Frame pair** from `capture.interface.get_frame_pair` / `read_frame`.
- **Species name and approved checks** from
  `knowledge.interface.get_species` and `get_approved_checks`.
- **Plant identity** (`species_code`) from `registry`.
- VLM inference via `mlops.assessment.run_probe` (`VLM_PROVIDER=local|vllm`).

## Outputs

- **Observation + trigger result** (VLM verdict, then Event Engine decision) → `advice`.

## Driven by

- `orchestrator` events / `main_worker` (run battery for a cycle).

## HTTP surface

`src/assessment/api.py` exposes `router` (prefix `/assessment`, superuser only),
mounted by `main_web`:

- `POST /assessment/plants/{plant_id}/run` — assess the latest two processed
  frames (`frames_run_id=None`), write an observation with a fresh `run_id`.
  No request body; does **not** run the rules engine. 404 unknown or archived plant, 409 skip,
  502 majority-invalid (`AssessmentRunError`), 503 Langfuse / prompt / VLM /
  MinIO (`AssessmentUnavailableError` or `CaptureDeviceError`).

## Notes

**Tier 2.** Debug observations are real observations of the plant: they count
in observation history (`get_previous_observation`, `count_observations` /
`dayCount`) and become the "previous" observation for the next scheduled
`evaluate_observation`. There is no exclusion from history; a test pins this.
The companion stage passes `excluding_run_id` so that same row is not also
the "previous" observation of the run that just wrote it.

## Internal design

Left to the context owner. Other contexts use only this context's published
interface; no access to the `assessment` schema from outside.
