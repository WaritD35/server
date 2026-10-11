# Vision Capture & Assessment Debug API Contract

> Author: plan workflow | Session: 20261011-173400 | Date: 2026-10-11
> Design: `docs/plans/designs/001-vision-assessment-port.md`

These routes run one pipeline step by hand, outside the orchestrator schedule. They
follow the existing context-router style (`registry/api.py`, `orchestrator/api.py`):
plain FastAPI responses, `HTTPException` errors, no `BaseResponse` envelope.

## Base URLs
- `/capture` — `src/capture/api.py`, `router = APIRouter(prefix="/capture", tags=["capture"])`
- `/assessment` — `src/assessment/api.py`, `router = APIRouter(prefix="/assessment", tags=["assessment"])`

## Authentication
All three operations require an admin user: `_user: CurrentSuperuser`
(`core.users.dependencies`). Non-admin → 403, missing/invalid token → 401.
Device tokens are not accepted.

---

## Endpoints

### POST /capture/plants/{plant_id}/run
- **Description**: Capture one frame from the plant's webcam, quality-check it, and
  (if it passes) preprocess, align and store it. `run_id` is NULL on the stored rows.
- **Request Body**: none
- **Response 200** (`CaptureRunRead`):
  ```json
  {
    "capture_id": "uuid",
    "plant_id": "uuid",
    "captured_at": "2026-10-11T07:05:00Z",
    "quality_passed": true,
    "quality_reason": null,
    "brightness": 118.4,
    "blur_score": 212.7,
    "processed_frame": {
      "id": "uuid",
      "object_key": "<plant_id>/2026/10/11/<frame_id>_whole_plant.jpg",
      "crop_type": "whole_plant",
      "width": 1024,
      "height": 1024,
      "aligned_to_frame_id": "uuid | null",
      "processed_at": "2026-10-11T07:05:02Z"
    }
  }
  ```
  `processed_frame` is `null` when `quality_passed` is `false`.
- **Errors**:
  - 404 plant not found or archived
  - 409 plant has no `device_id`, device not in `CAPTURE_WEBCAM_MAP` (empty map = capture disabled), or webcam busy
  - 503 webcam failed to open/read, or MinIO unavailable

### GET /capture/plants/{plant_id}/frames
- **Description**: Recent processed frames for a plant, newest first.
- **Query Params**: `limit` (int, 1–50, default 10)
- **Response 200**: `list[ProcessedFrameRead]` (same shape as `processed_frame` above, plus `capture_id`, `plant_id`, `run_id`)
- **Errors**: 404 plant not found

### POST /assessment/plants/{plant_id}/run
- **Description**: Run the VLM assessment (`mlops.assessment.run_probe`) on the plant's
  two latest processed frames (`capture.interface.get_frame_pair(session, plant_id, None)`)
  and store one `assessment.observation` with a fresh `run_id`. Does **not** run the
  rules engine, advice or companion.
- **Request Body**: none
- **Response 200** (`ObservationRead`, existing schema):
  ```json
  {
    "id": "uuid",
    "plant_id": "uuid",
    "run_id": "uuid",
    "timestamp": "2026-10-11T07:05:00Z",
    "health_status": "possibly_unhealthy",
    "confidence": 0.8,
    "observations": [
      {"type": "leaf_yellowing", "severity": "moderate", "description": "..."}
    ],
    "consensus": {"agreement": 0.8, "runs": 5, "model_stated_average": 0.72},
    "image_refs": ["s3://frames-derived/<prev>", "s3://frames-derived/<curr>"],
    "description": null,
    "companion_message": null,
    "created_at": "2026-10-11T07:06:10Z"
  }
  ```
- **Errors**:
  - 404 plant not found
  - 409 plant has no `species_code`; fewer than two processed frames exist
  - 502 majority of VLM runs returned invalid output
  - 503 Langfuse prompt or its config (`runs`, `temperature`, `max_new_tokens`, `model`) unavailable, or VLM backend unreachable

---

## Data Models

### ProcessedFrameRead
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | UUID | yes | `vision.processed_frame.id` |
| capture_id | UUID | yes | `vision.capture.id` |
| plant_id | UUID | yes | `registry.plant.id` |
| run_id | UUID | no | Orchestrator run; null for debug captures |
| object_key | string | yes | Key in the `frames-derived` bucket |
| crop_type | string | no | e.g. `whole_plant` |
| width / height | int | yes | Pixels |
| aligned_to_frame_id | UUID | no | Frame used as alignment reference |
| processed_at | datetime | yes | ISO 8601 UTC |

## Notes
- No raw file paths are accepted anywhere; frames are referenced by ID only.
- Image bytes are never returned by these routes.
