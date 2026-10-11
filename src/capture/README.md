# 2. Capture & Vision

**Package:** `src/capture/`
**Role:** Visual data extraction — turn uploaded frames into clean, comparable
inputs for assessment. (This is the **cloud** vision pipeline; the on-device
capture agent lives in the edge repository.)
**Owner:** _TBD_

## Responsibilities

- Receive capture batches uploaded from the edge (scheduled, several times a day,
  no manual intervention) and quality-check them.
- Preprocess frames.
- Segment the plant from the domestic background (on-prem segmentation service at
  `SEGMENTATION_ENDPOINT`; model not in this repo), producing named crops
  (e.g. `whole_plant`, `canopy_upper`, `canopy_lower`, `leaf_closeup_k`,
  `soil_surface`).
- Register and align the current frame against the reference so the two are
  directly comparable; composite onto a single labelled canvas where the VLM
  handles one image better than two.

## Data it owns

- PostgreSQL schema `vision`:
  - `vision.capture` — one webcam grab (quality result, raw object key, device).
  - `vision.processed_frame` — a grab that passed quality (crop, alignment,
    processed object key). Same-schema FK `capture_id` → `vision.capture.id`.
- MinIO: `captures` (raw JPEG), `frames-derived` (processed JPEG). Captures are
  retained. Keys: `{plant_id}/{yyyy}/{mm}/{dd}/{capture_id}.jpg` (raw) and
  `{plant_id}/{yyyy}/{mm}/{dd}/{frame_id}_{crop_type}.jpg` (processed).

## Inputs

- Capture-batch uploads, routed in by `orchestrator`.
- Plant identity / anchor from `registry`.
- Until edge uploads land: a server-side webcam. `capture.service.capture_for_plant`
  maps the plant's `device_id` to a webcam index through `CAPTURE_WEBCAM_MAP`
  (`"device-abc=0,device-def=1"`; empty, the default, disables capture), grabs one
  frame in memory, quality-checks it and, if it passes, crops the plant
  (`whole_plant`) and records the previous frame it is aligned to. Raises
  `CaptureUnavailable` (no device / unmapped), `CaptureBusy` (that webcam is in
  use) or `CaptureDeviceError` (webcam or MinIO failure). Nothing is written to
  local disk; object keys are `{plant_id}/{yyyy}/{mm}/{dd}/{capture_id}.jpg`
  (raw) and `{plant_id}/{yyyy}/{mm}/{dd}/{frame_id}_{crop_type}.jpg` (processed).

## Outputs

- **Extracted features** (segmented, aligned frames + crop manifest) → `assessment`.
- Published interface (`capture.interface`): `get_frame_pair(session, plant_id, run_id=None)`
  → `FramePair | None` (that run's frame and the one before it, or the latest two)
  and `read_frame(frame)` → JPEG bytes.

## Driven by

- The orchestrator `capture` stage (`capture_for_plant` with the run's `run_id`).
- The admin debug route (same function, `run_id=None`). Edge-batch drain is still planned.

## HTTP surface

`src/capture/api.py` exposes `router` (prefix `/capture`, superuser only), mounted
by `main_web`:

- `POST /capture/plants/{plant_id}/run` — one capture outside the schedule
  (404 plant, 409 unavailable/busy, 503 webcam or MinIO failure).
- `GET /capture/plants/{plant_id}/frames?limit=` — recent processed frames.

## Internal design

Left to the context owner. Other contexts use only this context's published
interface; no access to the `vision` schema from outside.
