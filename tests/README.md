# tests/

Mirrors `src/`: one subpackage per context (`tests/registry/`, `tests/capture/`,
…) plus `tests/api/` and shared fixtures in `tests/conftest.py`.

- Unit tests for a context live under that context's folder and stay within its
  boundary.
- `tests/capture/` — quality/preprocess on synthetic images, `capture_for_plant`
  with a fake webcam and MinIO, frame-pair selection, admin debug routes.
- `tests/mlops/` — knowledge runtime plus assessment: `test_assessment_aggregate.py`,
  `test_assessment_runtime.py` (fake prompt + backend), `test_assessment_isolation.py`.
- Tests that cross contexts (pipeline, event routing) go under `tests/integration/`.
- Run with `uv run pytest`.
