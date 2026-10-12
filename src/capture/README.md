# 2. Capture & Vision

**Package:** `src/capture/`
**Role:** Visual data extraction — turn the photos planter devices upload into
clean, comparable inputs for assessment. (The camera is on the planter; the
capture agent lives in the edge repository. Nothing here talks to a camera.)
**Owner:** _TBD_

## Responsibilities

Path: device upload → quality check → preprocess → stored, unclaimed →
claimed by the next pipeline run → paired with the previous run's frame for
`run_probe`.

- Receive a JPEG from a paired planter, resolve its plant through `registry`
  (`get_plant_by_device`, which also checks the device's current owner) and
  quality-check it at once, so the device learns straight away whether to
  retake the photo.
- When quality passes, crop the plant (`whole_plant`). The frame stays
  unclaimed (`run_id` `NULL`).
- When the pipeline's capture stage runs, claim the newest frame captured since
  the previous run's frame, and align it to that frame. Nothing new → the stage
  skips.
- On-prem segmentation (`SEGMENTATION_ENDPOINT`, named crops beyond
  `whole_plant`) and a single labelled canvas are deferred.

## Data it owns

- PostgreSQL schema `vision`:
  - `vision.capture` — one uploaded photo (quality result, raw object key,
    device, `captured_at` from the device clock, `run_id` once claimed).
  - `vision.processed_frame` — a photo that passed quality (crop, alignment,
    processed object key, `run_id` once claimed). Same-schema FK `capture_id` →
    `vision.capture.id`.
- MinIO: `captures` (raw JPEG, exactly as uploaded), `frames-derived` (processed
  JPEG). Captures are retained. Keys: `{plant_id}/{yyyy}/{mm}/{dd}/{capture_id}.jpg`
  (raw) and `{plant_id}/{yyyy}/{mm}/{dd}/{frame_id}_{crop_type}.jpg` (processed),
  dated by `captured_at`.

## Inputs

- Photo uploads from planters (`POST /capture/devices/me/frames`, device token).
- Plant identity and device binding from `registry`.

`capture.service.ingest_frame` rejects (`InvalidUpload` → 422) a body that is
not a JPEG, cannot be decoded, or has a `captured_at` more than 5 minutes ahead
of the server clock; an upload over 10 MB is 413. A photo that fails the quality
gate (too small, too dark or bright, blurry, no plant in frame) is still stored,
with `quality_passed=False` and the reasons, and answers 201 so the device can
retake it. MinIO failure raises `CaptureDeviceError` (503). Nothing is written to
local disk.

## Outputs

- **Extracted features** (cropped, aligned frames) → `assessment`.
- Published interface (`capture.interface`): `get_frame_pair(session, plant_id, run_id=None)`
  → `FramePair | None` and `read_frame(frame)` → JPEG bytes. With `run_id`, the
  pair is that run's frame and the previous **run's** frame (frames no run
  claimed are skipped), so each assessment compares against the one before it;
  without, the latest two frames (debug).

## Driven by

- Planter devices (uploads, any time).
- The orchestrator `capture` stage: `claim_frame(session, plant_id, run_id)`
  ties the newest frame captured since the previous run's to the run, or returns
  `None` → `StageSkipped("no new photo uploaded since the previous run")`. Older
  unclaimed frames stay unclaimed.

## HTTP surface

`src/capture/api.py` exposes `router` (prefix `/capture`), mounted by `main_web`:

| Method & path | Caller | Purpose |
|---|---|---|
| `POST /capture/devices/me/frames` | device (`CurrentDevice`) | upload a photo (multipart `image`, optional `captured_at`) of the plant bound to the device. 404 if none |
| `POST /capture/plants/{plant_id}/frames` | superuser | upload a photo for a plant by hand (testing without a device). 404 unknown or archived plant |
| `GET /capture/plants/{plant_id}/frames?limit=` | superuser | recent processed frames |

Both uploads answer 201 with the capture's quality result and its processed
frame (or `null`).

## Internal design

Left to the context owner. Other contexts use only this context's published
interface; no access to the `vision` schema from outside.
