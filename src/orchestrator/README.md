# orchestrator — the scheduled pipeline

**Package:** `src/orchestrator/`
**Part of context 8 (System Integration & Infrastructure).**

## What it does

At each time in `config/orchestrator.toml`, every live plant (from `registry`)
gets a **run**, and the run calls the pipeline stages in the order the
architecture fixes:

```
capture (preprocess) → assessment → advice → companion
```

Each context stores its own result in its own schema, so nothing is passed
between stages but the run's identifiers (`StageInput`: `run_id`, `plant_id`,
`scheduled_for`). A plant goes through all four stages before the next plant
starts.

- **Owns (Postgres schema):** `orchestrator` — `pipeline_run`
- **Calls:** `registry.list_plant_ids`, and one function per stage context
- **Driven by:** `OrchestratorLoop`, a background thread `main_web` starts

> Status: PoC. `run_capture` and `run_assessment` are wired to their contexts;
> advice and companion already call theirs. Errors are not handled beyond
> recording them — see below.

## Writing a stage

Replace the stub in `stages.py` with a call to your context's function.
Capture and assessment already do:

```python
def run_capture(payload: StageInput) -> None:
    result = capture_for_plant(session, payload.plant_id, payload.run_id)

def run_assessment(payload: StageInput) -> None:
    assess_frames(session, payload.plant_id, payload.run_id, frames_run_id=payload.run_id)
    evaluate_observation(session, payload.plant_id, payload.run_id)
```

- **Return** when your result is stored — the next stage runs.
- **Raise `StageSkipped("reason")`** when there is nothing to do (e.g. no new
  frames): the run ends as `skipped` and the later stages do not run.

### Capture and assessment

Every stage imports from the context's package (`from capture import …`), never
from its `service` / `models` modules.

- `run_capture` calls `capture.capture_for_plant`. Raises `StageSkipped`
  when `CAPTURE_WEBCAM_MAP` is empty, the device is unmapped, the webcam is
  busy, or the quality check fails (the `vision.capture` row is still stored).
  `CaptureDeviceError` (webcam / MinIO) is not skipped — the run **fails**.
- `run_assessment` calls `assessment.assess_frames` (VLM probe, with
  `frames_run_id=run_id`) then `evaluate_observation` (0-token rules). Raises
  `StageSkipped` when species is unset or fewer than two processed frames exist
  (baseline). `AssessmentRunError` and `AssessmentUnavailableError` fail the run.

**Tier 2.** `VLM_PROVIDER=local` runs the inferences in-process on the
orchestrator loop thread; on CPU this can take minutes. Development only;
production uses `vllm`.

## Configuration — `config/orchestrator.toml`

Re-read on every tick, so edits apply without a restart. Path from the
`ORCHESTRATOR_CONFIG` setting.

| Key | Meaning |
|---|---|
| `enabled` | `false` = the loop idles |
| `poll_seconds` | how often the loop wakes up |
| `timezone`, `schedule` | when runs are queued (wall-clock times in that zone) |
| `catch_up_minutes` | a slot is still queued this late (e.g. the server was restarting); after that it is skipped |
| `stale_after_minutes` | a run still `running` this long after it started is marked `failed` (default 60); keep it above the slowest run |

## Data model

`pipeline_run` — one plant × one slot:

```
queued → running → succeeded
             ├───→ skipped    a stage raised StageSkipped
             └───→ failed     a stage raised anything else,
                              or the run was abandoned (see Errors)
```

- `UNIQUE (plant_id, scheduled_for)`: a slot is queued once, however many ticks
  or processes see it.
- `current_stage` is where the run is, or where it stopped; `detail` holds the
  error or skip reason.
- A run is claimed with a conditional `UPDATE … WHERE status = 'queued'`, so two
  web processes never execute the same run.

## Errors (PoC)

A stage that raises marks its run `failed` (the error goes to `detail` and the
log, with its traceback) and the loop moves on to the next plant. Nothing is
retried and a running stage is not timed out.

**Abandoned runs.** A run that was executing when its process stopped would
otherwise stay `running` forever. Every tick first marks runs still `running`
`stale_after_minutes` after `started_at` as `failed`, with
`detail = "<stage>: abandoned — …"`; `current_stage` still says where it
stopped. This is not done at startup, because with several web processes a
`running` run may belong to one that is alive.

**Finding a run in Langfuse.** The assessment, advice and companion traces carry
`run_id` (and `plant_id`) in their metadata, so a failed run's VLM / LLM calls
can be found in each component's Langfuse project by filtering on
`metadata.run_id`.

## HTTP API (`/orchestrator`, superuser only)

| Method & path | Purpose |
|---|---|
| `GET /orchestrator/runs?status=&plant_id=&limit=` | runs, newest first |
| `GET /orchestrator/runs/{id}` | one run |
| `POST /orchestrator/runs` `{plant_id}` | queue a run now, outside the schedule (picked up on the next tick) |
| `GET /orchestrator/schedule` | the schedule in effect, and the next slot |

## Internal design

```
src/orchestrator/
├── api.py       # /orchestrator router
├── worker.py    # tick() = queue due slots → execute queued runs; OrchestratorLoop
├── service.py   # schedule arithmetic + the queue (enqueue / claim / finish / reads)
├── stages.py    # STAGES (order), StageInput, StageSkipped, the stage functions
├── config.py    # load_config() / parse_config() for config/orchestrator.toml
├── schemas.py   # RunRead / RunCreate / ScheduleRead
├── models.py    # PipelineRun
└── db.py        # ORCHESTRATOR_SCHEMA, Base, utcnow(), as_utc(), init_models()
```

`main_worker.py` is not part of this: it is a separate entrypoint for forcing
work by hand. `tick()` can be called directly for the same purpose.

## Not done yet

- **Device sync / presentation sync** (the other half of this context) — the
  device-facing `GET /sync` discussed with `registry`'s device pairing.
