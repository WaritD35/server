# tests/

Mirrors `src/`: one subpackage per context (`tests/registry/`, `tests/capture/`,
…) plus `tests/api/` (the companion-state endpoint) and `tests/mlops/` (the
shared AI library). Fixtures live in each subpackage's `conftest.py`.

- Unit tests for a context live under that context's folder and stay within its
  boundary.
- `tests/capture/` — quality/preprocess on synthetic images, device uploads
  (`ingest_frame`) and the capture stage's `claim_frame` with a fake MinIO,
  frame-pair selection, device and admin routes.
- `tests/mlops/` — every VLM / LLM runtime with a fake prompt and model,
  aggregation, prompt seeding, and import-isolation checks (the runtimes and
  experiment CLIs must not pull in a database or a bounded context).
  `test_scenarios_mock.py` runs the scenarios in
  `fixtures/mock_llm_for_llm_pipeline_integration/` through the pipeline with a
  mock LLM.
- `tests/orchestrator/test_pipeline_e2e.py` — the four stages end to end on
  SQLite with the VLM stubbed.
- `tests/postman/` — a Postman collection for manual API checks (not run by
  pytest).
- Run with `uv run pytest`.
