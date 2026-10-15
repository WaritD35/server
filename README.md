# Pet-Plant — Server

Cloud backend for **Pet-Plant**: raising a houseplant like a pet through a
VLM-based plant-health assessment pipeline and a character-mediated interface.

A camera in the planter captures the plant several times a day and uploads the
frames to infrastructure this project controls. On that infrastructure the image
is segmented, the species is resolved once, a battery of single-question probes is
run against an on-prem VLM API, and the structured results are turned into ranked
care actions and words for an on-device character. **The image itself is never
sent to a third-party API** — only a few hundred bytes of verdicts, severities and
evidence sentences ever leave.

This repository is the **server side** (cloud backend + the device-facing API).
The edge agents that run on the planter (capture, ambient display, device
management) live elsewhere.

> Status: early scaffolding. The bounded contexts below are fixed; the internal
> design of each is owned by its context lead. See
> [`docs/Pet-Plant_Proposal_v2.docx`](docs/Pet-Plant_Proposal_v2.docx) for the
> full proposal, milestones and week-by-week plan.

---

## Architecture at a glance

The system is a **modular monolith** organised into nine top-level Python
packages: eight DDD bounded contexts plus a shared infrastructure kernel
(`core`). There is no central HTTP layer — each context owns its HTTP surface in
`<context>/api.py` and its own schemas, and `main_web` assembles them.
`docs/architecture.svg` is the source of truth for context boundaries and data
flow.

### The assessment pipeline

```
planter ──POST /capture/devices/me/frames──▶ capture (vision: quality check, crop)
                                                    │  claimed by the next run
        orchestrator ──────────────────────────────▶│
        registry ──plant identity / device──────────▶│
        knowledge ──approved structured checks──────▶ assessment (VLM probe, JSON schema, then 0-token rules)
        capture  ──aligned frame pair───────────────────────▶│
                                                              │
                                internal state, verdicts, fired checks + approved actions ▶ advice (LLM)
                                          diagnosis & actions ─▶ companion
                                             mood + utterances ─▶ orchestrator ──▶ edge display

        mlops ──runs every VLM / LLM call above (VLM_PROVIDER=local|vllm), and the experiments behind them
```

### Bounded contexts

| # | Context | Package | Role | Owns (Postgres schema) | Owns (MinIO) |
|---|---------|---------|------|------------------------|--------------|
| 1 | Plant Registry | `src/registry` | Identity & physical anchoring — plants, owners, species assignment, camera placement | `registry` | — |
| 2 | Capture & Vision | `src/capture` | Visual data extraction — photos uploaded by planter devices (`POST /capture/devices/me/frames`), quality-check, preprocess, align frames | `vision` | `captures`, `frames-derived` |
| 3 | Species & Care Knowledge | `src/knowledge` | Botanical knowledge base — care needs, probe authoring (approved probe set per species: checks, their basis and approved actions) | `knowledge` | — |
| 4 | Assessment | `src/assessment` | Internal-state inference — run the approved checks on the VLM, validate each JSON answer, aggregate N runs into verdict + severity + evidence + agreement | `assessment` | — |
| 5 | Care Advice | `src/advice` | Interpretable reasoning — turn verdicts + history into a diagnosis, ranked actions, rationale (external LLM, chooses only from the approved actions of the checks that fired) | `advice` | — |
| 6 | Companion | `src/companion` | Ambient UI & persona — map internal state to moods / expressions / utterances, run the interaction loop, process care acknowledgements | `companion` | — |
| 7 | MLOps | `src/mlops` | **Not a bounded context — a shared library.** Every VLM / LLM call in the system, plus the prompt management, tracing and offline experiments behind them (Langfuse). One subpackage per LLM-using component | — (none: Langfuse holds it) | `eval-sets` |
| 8 | System Integration & Infrastructure | `src/orchestrator` + `src/core` | End-to-end orchestration & operations — event routing, batch workflow, device sync (`orchestrator`); cross-cutting infrastructure (`core`) | `orchestrator` (event outbox / job runs / device sync), `auth` (`core`) | — |

**HTTP surface.** `main_web` builds the FastAPI app: it mounts `core`'s health
router and, as each lands, every context's router. Each context owns
`src/<context>/api.py` exposing `router: APIRouter`, plus its own request/response
schemas — organised however that context's lead prefers. Routers apply the auth
dependencies `core` provides. `core` is not a context: it holds cross-cutting
infrastructure and the `auth` schema.

---

## Repository layout

```
pet-plant-server/
├── README.md
├── compose.yaml              # `docker compose up` → Postgres + MinIO (+ buckets) + web
├── pyproject.toml            # project metadata, fixed deps, tooling (uv, Python 3.12, ruff, mypy, pytest)
├── uv.lock                   # dependency lockfile — committed
├── .env.example              # config schema + committed defaults (owned by core)
├── .gitignore
├── .dockerignore
├── docker/
│   └── Dockerfile            # one image; web = default CMD, worker = command override
├── docs/
│   ├── README.md             # index of project documents
│   ├── architecture.svg      # context / data-flow diagram — source of truth for boundaries
│   ├── Pet-Plant_Proposal_v2.docx
│   └── context_and_tasks.docx
├── scripts/                  # operational and one-off scripts
├── tests/                    # mirrors src/ — one subpackage per context
└── src/                      # source root; each context is a top-level importable package
    ├── main_web.py           # FastAPI app: mounts core health + each context router (uvicorn target)
    ├── main_worker.py        # batch / worker entrypoint — runs the job scheduled for "now"
    ├── core/                 # infrastructure kernel (config, DB, storage, auth, logging); core/api.py = health
    ├── orchestrator/         # event bus, message routing, batch-workflow coordination, device sync
    ├── registry/             # 1. Plant Registry
    ├── capture/              # 2. Capture & Vision (cloud vision pipeline)
    ├── knowledge/            # 3. Species & Care Knowledge
    ├── assessment/           # 4. Assessment (VLM probe execution)
    ├── advice/               # 5. Care Advice (external LLM)
    ├── companion/            # 6. Companion (persona / ambient-UI backend)
    └── mlops/                # 7. MLOps — all VLM/LLM calls + experiments (Langfuse)
```

Every context that exposes HTTP puts its routes in `src/<context>/api.py`
(exposing `router: APIRouter`) and keeps its request/response schemas within the
context. `main_web` imports and mounts those routers.

Each context directory carries its own `README.md` stating its responsibilities,
the data it owns, its inputs and outputs, and what drives it. **The module layout
inside a context is decided by that context's lead** and is deliberately left
unspecified here.

---

## Runtime processes

The repository produces two entrypoints. Both load the same code and the same
configuration; they differ only in what they start.

### `src/main_web.py` — web

Assembles the FastAPI application (`create_app()`), mounting `core`'s health
router and each context's `api.py` router, and serves the HTTP surface:
device-facing endpoints (capture upload, presentation sync), owner-facing
endpoints (plant registration, companion state, care acknowledgements) and
internal operations endpoints. Run under `uvicorn`.

### `src/main_worker.py` — worker

Runs the batch job that is due **at the moment it is invoked**. It is a
dispatcher, not a long-lived scheduler: an external scheduler (cron or a systemd
timer) calls it with the job to run. Jobs include, among others: draining pending
capture batches through segmentation and alignment, running the screening
probe and probe battery, and generating draft probe sets for research documents
that have none yet (`knowledge.generation.generate_pending` — today it is also
reachable as `POST /knowledge/probe-sets/generate` for manual runs). The concrete
job catalogue is owned by `orchestrator`.
Prompt experiments are not jobs — they are run on demand with
`python -m mlops.<component>.experiment`.

---

## Tech stack

| Concern | Choice | Notes |
|---------|--------|-------|
| Language | Python 3.12 | |
| Packaging | **uv** | `pyproject.toml` + committed `uv.lock` |
| Web framework | **FastAPI** | fixed |
| ORM / DB toolkit | **SQLAlchemy** | fixed |
| Validation / models | **Pydantic** | fixed |
| Relational store | **PostgreSQL** | one instance, one schema per context |
| Object store | **MinIO** | S3-compatible; frames, derived crops, evaluation sets |
| LLMOps | **Langfuse** | Prompt management, tracing, datasets, scores — see `src/mlops`. Cloud or self-hosted; `LANGFUSE_HOST` decides |
| LLM framework | **LangChain** (+ `langchain-openai`) | Chains / agents; v1 bundles `langgraph`. Used only inside `src/mlops` |

Beyond FastAPI / SQLAlchemy / Pydantic, additional libraries are each context's
own choice — except the AI stack, which is pinned because it lives once in
`src/mlops` and every LLM-using context calls into it.

---

## Data & storage

### PostgreSQL — schema per context

A single PostgreSQL instance holds one schema per context (`registry`, `vision`,
`knowledge`, `assessment`, `advice`, `companion`, `orchestrator`) plus an
`auth` schema owned by `core`. `mlops` has no schema — Langfuse holds its
prompts, traces, datasets and scores. A context reads and writes **only its own
schema**. Cross-context data is obtained through the owning context's published
interface, never by querying another schema or joining across schemas. Migrations
are per-schema and owned by each context; `core` provides the migration
tooling and wiring.

### MinIO — buckets

Indicative bucket layout (exact names/prefixes finalised by the owning contexts):

| Bucket | Owner | Contents |
|--------|-------|----------|
| `captures` | capture | Raw uploaded frames / capture batches |
| `frames-derived` | capture | Segmented crops, aligned and composited canvases |
| `eval-sets` | mlops | Images referenced by Langfuse dataset items (dataset item JSON lives in Langfuse) |

Retention: captures are kept in our own store so owner corrections become new
evaluation cases.

---

## Inter-context communication

**Synchronous** — in-process calls through a context's published interface only
(no reaching into another context's internals or schema), or HTTP against the
context's own `api.py` router (assembled by `main_web`).

**Asynchronous** — the `orchestrator` event bus. It routes the edge→cloud capture
upload to `capture`, carries pipeline hand-offs where a step is event-driven, and
delivers `companion` mood/utterance updates back to the edge display
("presentation sync"). Message contracts are defined by `orchestrator` together
with the producing and consuming contexts.

The pipeline order (`capture → assessment → advice → companion`) is fixed by the
architecture; whether each hand-off is a direct call or an event is an
implementation choice coordinated with `orchestrator`.

---

## Authentication & authorization

A single **OAuth2 / JWT Bearer** scheme for every caller, with authorization by
**role**:

| Role | Used by | Typical access |
|------|---------|----------------|
| `device` | Edge planters | Capture upload, presentation sync, device-scoped reads — restricted to the plant(s) bound to that device |
| `owner` | Owner-facing clients | Their own plants: registration, companion state, care acknowledgements |
| `ops` / `admin` | Project operators | Knowledge authoring, evaluation and promotion, cross-plant operations |

`core` owns token issuance and verification and the `auth` schema (identities,
roles, device bindings), and exposes the FastAPI auth dependencies that each
context applies in its `api.py`. Tokens are scoped so a `device` or `owner` token
cannot reach another household's data.

**Hard boundary:** no plant imagery is ever transmitted to a third-party service.
Segmentation and the VLM run on infrastructure this project controls. The
`advice` and `companion` contexts, and offline probe generation in `knowledge`
call a text model. They send text (verdicts, severities, evidence, history,
or approved structured knowledge) — never images.

---

## External services

This repository contains **clients and configuration only** — no model weights and
no inference runtime. Each service below is deployed and operated separately;
contexts reach it over the network using values from `.env`.

| Service | Hosting | Interface | Used by |
|---------|---------|-----------|---------|
| VLM (probe execution) | On-prem **vLLM**, or in-process transformers | `VLM_PROVIDER=vllm` → OpenAI-compatible HTTP (`VLM_API_BASE`); `VLM_PROVIDER=local` → weights at `VLM_LOCAL_MODEL_PATH` (`uv sync --group vlm-local`) | `mlops` (for assessment) |
| Segmentation model | On-prem | HTTP API (`SEGMENTATION_ENDPOINT`) | capture |
| LLM (advice, companion, probe generation) | External API or local Ollama, text only | `LLM_PROVIDER=openai` uses `LLM_API_BASE`; `LLM_PROVIDER=ollama` uses `OLLAMA_HOST` | `mlops` (for advice, companion, knowledge) |
| Langfuse (prompts, traces, datasets, scores) | Langfuse Cloud or self-hosted (`LANGFUSE_HOST`) | Langfuse SDK | `mlops` — one project **per component**, so one key pair each |

---

## Local development

Run the whole stack — PostgreSQL, MinIO (+ buckets) and the FastAPI service — with
one command from the repo root:

```bash
docker compose up --build            # add -d to detach; drop --build after the first run
```

`web` comes up on <http://localhost:8000> (`/health`, `/docs`) once `postgres` and
`minio` report healthy. Committed defaults live in `.env.example` (read by
`core.config`), so the stack starts with no `.env`. To override locally:

```bash
cp .env.example .env                 # optional; then edit
```

Run a worker job ad hoc:

```bash
docker compose run --rm worker python src/main_worker.py <job-name>
```

For host-side work (tests, linting, autoreload) use uv against the containerised
infrastructure:

```bash
uv sync                              # .venv from the committed uv.lock (deps + dev group)
uv sync --group vlm-local            # optional: in-process InternVL when VLM_PROVIDER=local
uv run pytest
uv run ruff check .
uv run mypy
uv run uvicorn main_web:app --app-dir src --reload
```

Relevant environment variables (full list in `.env.example`):

| Variable | Meaning |
|----------|---------|
| `VLM_PROVIDER` | `vllm` (default) or `local` |
| `VLM_API_BASE` / `VLM_API_KEY` | OpenAI-compatible vLLM endpoint |
| `VLM_LOCAL_MODEL_PATH` | Weights for `local` (default `models/internvl3_5-2b`) |
| `LLM_PROVIDER` | `openai` (default) or `ollama` for chat models |

---

## What is intentionally not decided here

- The internal module structure of each bounded context — owned by its lead.
- How each context organises its HTTP routes and request/response schemas — only
  the entry point is fixed: `src/<context>/api.py` exposing `router: APIRouter`.
- Libraries beyond the fixed baseline in `pyproject.toml` — each context adds its
  own (segmentation clients, etc.). VLM / LLM / Langfuse clients are **not** among
  them: those live once in `src/mlops`, and contexts call into it.
- Concrete event names, job names and interface signatures — defined by the
  owning contexts as work begins.
- Whether individual pipeline hand-offs are synchronous calls or events.

