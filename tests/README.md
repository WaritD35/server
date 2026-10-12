# tests/

Mirrors `src/`: one subpackage per context (`tests/registry/`, `tests/capture/`,
…) plus `tests/api/` and shared fixtures in `tests/conftest.py`.

- Unit tests for a context live under that context's folder and stay within its
  boundary.
- `tests/capture/` — quality/preprocess on synthetic images, device uploads`
  (`ingest_frame`) and the capture stage's `claim_frame` with a fake MinIO,
  frame-pair selection, device and admin routes.
- `tests/mlops/` — knowledge runtime plus assessment: `test_assessment_aggregate.py`,
  `test_assessment_runtime.py` (fake prompt + backend), `test_assessment_isolation.py`.
- Tests that cross contexts (pipeline, event routing) go under `tests/integration/`.
- Run with `uv run pytest`.
