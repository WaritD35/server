# Pet Plant Server — Architecture & Data Flow Specification

**Project:** `pet-plant-server`
**Document:** System Architecture, Bounded Contexts, and Database Specification
**Status:** Approved Architecture (Production Target)
**Date:** October 2026

---

## 1. System Overview

`pet-plant-server` is the cloud backend platform for the Pet Plant system. It coordinates upstream camera perception (VLM), deterministic botanical event evaluation, LLM-based care advisory, first-person character companion dialogue, and user action tracking.

### Core Architectural Principles

1. **Modular Monolith with Bounded Contexts:** Each business domain lives in an isolated folder under `src/` with its own private PostgreSQL schema, SQLAlchemy metadata, and internal service logic.
2. **Strict Context Isolation (No Cross-Schema Foreign Keys):** Bounded contexts never share database foreign keys. All cross-context references use immutable `UUID` values, and data dependencies are resolved via published Python interfaces (`<context>.interface`).
3. **UUID identifiers & timezone timestamps:** Table primary keys are native PostgreSQL `UUID` values (`uuid.uuid4()`), except `knowledge.species`, whose primary key is `species_code` (`TEXT`). Every timestamp is a timezone-aware `TIMESTAMPTZ` (`DateTime(timezone=True)`) normalized to UTC.
4. **Deterministic Gatekeeper Pattern:** The VLM probe (`mlops.assessment.run_probe`) runs first and writes an observation. The Event Engine and milestone detection that follow are 0-token Python, so a healthy/stable scan does not invoke the advice or companion LLMs.
5. **Approved, structured knowledge only:** Checks and care steps come from the approved probe set through `get_approved_checks`: assessment gets every approved check, advice only the checks that fired, each with its approved basis (`evidence_quote`) and its approved actions in order. Nothing is searched and no research text is sent. `get_care_knowledge` is not used by the pipeline.
6. **Alembic Multi-Schema Migrations:** All database schemas, extensions (`vector`), tables, and indexes are managed through version-controlled Alembic migrations (`include_schemas=True`).

---

## 2. Complete Entity-Relationship (ER) Diagram

```mermaid
erDiagram
    %% Auth Schema
    "auth.users" {
        UUID id PK
        TEXT email UK
        TEXT name
        TEXT hashed_password
        BOOLEAN is_active
        BOOLEAN is_superuser
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    "auth.devices" {
        UUID id PK
        TEXT physical_id UK
        UUID owner_id FK
        TEXT name
        TEXT status
        TEXT token_hash UK
        TIMESTAMPTZ paired_at
        TIMESTAMPTZ revoked_at
        TIMESTAMPTZ last_seen_at
        TIMESTAMPTZ created_at
    }

    "auth.device_pairings" {
        UUID id PK
        TEXT physical_id
        TEXT user_code
        TEXT device_code_hash UK
        TEXT status
        TIMESTAMPTZ expires_at
        UUID device_id FK
        TIMESTAMPTZ created_at
    }

    %% Registry Schema
    "registry.plant" {
        UUID id PK
        UUID owner_id "auth.users.id"
        TEXT name
        TEXT species_code "knowledge.species.species_code"
        TIMESTAMPTZ species_confirmed_at
        TEXT device_id "auth.devices.physical_id"
        TEXT note
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
        INTEGER level
        REAL xp_ratio
        TIMESTAMPTZ archived_at
    }

    %% Vision Schema (owned by capture)
    "vision.capture" {
        UUID id PK
        UUID plant_id "registry.plant.id"
        UUID run_id "orchestrator.pipeline_run.id"
        TEXT device_id
        TIMESTAMPTZ captured_at
        TEXT raw_object_key
        INTEGER width
        INTEGER height
        REAL brightness
        REAL blur_score
        BOOLEAN quality_passed
        TEXT quality_reason
        TIMESTAMPTZ created_at
    }

    "vision.processed_frame" {
        UUID id PK
        UUID capture_id FK
        UUID plant_id "registry.plant.id"
        UUID run_id
        TEXT object_key
        TEXT crop_type
        INTEGER width
        INTEGER height
        UUID aligned_to_frame_id
        TIMESTAMPTZ processed_at
    }

    %% Assessment Schema
    "assessment.observation" {
        UUID id PK
        UUID plant_id "registry.plant.id"
        UUID run_id "orchestrator.pipeline_run.id"
        TIMESTAMPTZ timestamp
        TEXT health_status
        REAL confidence
        JSONB observations_json
        JSONB consensus_json
        JSONB image_refs_json
        UUID probe_set_id "knowledge.probe_set.id"
        JSONB probe_results_json
        TEXT description
        TEXT companion_message
        TIMESTAMPTZ created_at
    }

    "assessment.trigger_result" {
        UUID id PK
        UUID run_id UK
        UUID plant_id
        TEXT decision
        TEXT primary_symptom
        REAL confidence
        TEXT reasoning
        TIMESTAMPTZ created_at
    }

    "assessment.plant_milestone" {
        UUID id PK
        UUID plant_id "registry.plant.id"
        UUID run_id
        TIMESTAMPTZ timestamp
        TEXT event_type
        TEXT description
        TIMESTAMPTZ resolved_at
        TIMESTAMPTZ created_at
    }

    %% Advice Schema
    "advice.care_plan" {
        UUID id PK
        TEXT care_plan_id
        UUID plant_id "registry.plant.id"
        UUID run_id UK
        TEXT status_label
        TEXT assessment
        REAL confidence
        JSONB actions_json "deprecated/compat"
        TIMESTAMPTZ created_at
    }

    "advice.care_plan_action" {
        UUID id PK
        UUID care_plan_pk FK
        TEXT care_plan_id
        TEXT action_id
        INTEGER priority
        TEXT action
        TEXT label
        TEXT action_type
        UUID approved_action_id "knowledge.probe_action.id"
        TEXT check_slug
        TEXT urgency
        INTEGER expect_typical_hours
        INTEGER expect_max_hours
        TIMESTAMPTZ created_at
    }

    "advice.diagnosis" {
        UUID id PK
        UUID plant_id "registry.plant.id"
        UUID run_id
        TEXT diagnosis
        TIMESTAMPTZ created_at
    }

    %% Companion Schema
    "companion.messages" {
        UUID id PK
        UUID plant_id "registry.plant.id"
        UUID run_id
        TEXT decision
        TEXT message
        TEXT source
        TIMESTAMPTZ created_at
    }

    %% Action Schema
    "action.care_event" {
        UUID id PK
        UUID plant_id "registry.plant.id"
        TEXT event_type
        TEXT care_plan_id
        TEXT action_id
        TEXT action_type
        TEXT client_event_id UK
        JSONB details
        TIMESTAMPTZ occurred_at
        TIMESTAMPTZ recorded_at
        UUID recorded_by_user_id
        UUID recorded_by_device_id
    }

    %% Knowledge Schema
    "knowledge.species" {
        TEXT species_code PK
        TEXT scientific_name
        TEXT common_name
    }

    "knowledge.research_document" {
        UUID id PK
        TEXT species_code FK
        TEXT title
        TEXT body
        TEXT content_hash
        TEXT author
        TEXT source_url
        TEXT source_note
        TEXT status
        TIMESTAMPTZ created_at
        TIMESTAMPTZ archived_at
    }

    "knowledge.probe_set" {
        UUID id PK
        UUID research_document_id FK
        TEXT species_code FK
        TEXT source_content_hash
        TEXT llm_model
        TEXT status
        TIMESTAMPTZ generated_at
    }

    "knowledge.probe" {
        UUID id PK
        UUID probe_set_id FK
        TEXT slug
        TEXT care_need
        INTEGER priority
        BOOLEAN is_screening
        TEXT question
    }

    "knowledge.probe_action" {
        UUID id PK
        UUID probe_id FK
        INTEGER ordering
        TEXT instruction
        TEXT urgency
        INTEGER expect_typical_hours
        INTEGER expect_max_hours
    }

    "knowledge.probe_exemplar" {
        UUID id PK
        UUID probe_id FK
        TEXT role
        TEXT storage_key UK
        TEXT origin
    }

    "knowledge.knowledge_chunks" {
        UUID id PK
        TEXT species_code
        TEXT topic
        TEXT content
        JSONB metadata_json
        VECTOR_1536 embedding
        UUID research_document_id FK
        UUID probe_set_id FK
        TEXT source_content_hash
        TIMESTAMPTZ created_at
    }

    %% Orchestrator Schema
    "orchestrator.pipeline_run" {
        UUID id PK
        UUID plant_id "registry.plant.id"
        TIMESTAMPTZ scheduled_for
        TEXT trigger
        TEXT status
        TEXT current_stage
        TEXT detail
        TIMESTAMPTZ created_at
        TIMESTAMPTZ started_at
        TIMESTAMPTZ finished_at
    }

    %% Logical Relationships
    "auth.users" ||--o{ "auth.devices" : "owns"
    "auth.devices" ||--o{ "auth.device_pairings" : "pairing attempt"
    "auth.users" ||--o{ "registry.plant" : "owns (owner_id)"
    "auth.users" ||--o{ "action.care_event" : "logged_by"
    "knowledge.species" ||--o{ "knowledge.research_document" : "documented_as"
    "knowledge.research_document" ||--o{ "knowledge.probe_set" : "generates"
    "knowledge.probe_set" ||--o{ "knowledge.probe" : "contains"
    "knowledge.probe" ||--o{ "knowledge.probe_action" : "remedies"
    "knowledge.probe" ||--o{ "knowledge.probe_exemplar" : "illustrated_by"
    "vision.capture" ||--o{ "vision.processed_frame" : "yields"
    "registry.plant" ||--o{ "vision.capture" : "device_uploads"
    "registry.plant" ||--o{ "assessment.observation" : "daily_snapshots"
    "registry.plant" ||--o{ "assessment.plant_milestone" : "life_events"
    "registry.plant" ||--o{ "advice.care_plan" : "prescribed_plans"
    "advice.care_plan" ||--o{ "advice.care_plan_action" : "contains (1:N)"
    "advice.care_plan_action" ||--o{ "action.care_event" : "completed_by (logical)"
    "registry.plant" ||--o{ "action.care_event" : "care_history"
    "registry.plant" ||--o{ "orchestrator.pipeline_run" : "runs"
    "orchestrator.pipeline_run" ||--|| "assessment.trigger_result" : "produces"
    "orchestrator.pipeline_run" ||--o| "advice.care_plan" : "generates"
    "orchestrator.pipeline_run" ||--o| "companion.messages" : "generates"
```

---

## 3. Bounded Context Map & Responsibilities

| Context | Schema | Primary Responsibility | LLM / Inference? |
|---|---|---|---|
| **`core`** | `auth` | User accounts, authentication, JWT tokens, hardware device provisioning | No |
| **`registry`** | `registry` | Plant profiles (`POST /registry/plants`), species binding, device binding, note, gamification (`level`, `xp_ratio`). Delete archives the row | No |
| **`capture`** | `vision` | Planter upload (`POST /capture/devices/me/frames`), quality, crop, alignment on claim; raw + processed frames in object storage. On-prem segmentation is deferred | No |
| **`assessment`** | `assessment` | VLM probe via `mlops.assessment.run_probe` (N runs from the Langfuse prompt config + consensus), then 0-token Event Engine and milestone detection. Asks the approved checks from `get_approved_checks` (structured; no search). Each VLM JSON answer is checked against the observation JSON Schema | VLM probe (mlops) + 0-token rules |
| **`advice`** | `advice` | Care Advisor. Runs only for `CARE_ADVICE_REQUIRED`. Fetches only the fired checks (basis + approved actions) with `get_approved_checks`; steps come from those `knowledge.probe_action` rows | ✅ Ollama / OpenAI via `mlops/advice/` + Langfuse |
| **`companion`** | `companion` | 1st-person plant character persona projection, two-tier memory, fact validator. Table is `companion.messages` | ✅ Ollama / OpenAI via `mlops/companion/` + Langfuse |
| **`action`** | `action` | Append-only care logs (`care_event`), button click deduplication, watering timestamps | No |
| **`knowledge`** | `knowledge` | Species catalogue, research documents, probe sets, probe actions, exemplar photos. The pipeline reads only the approved probe set, through `get_approved_checks` | LLM generates probe drafts |
| **`orchestrator`**| `orchestrator`| Schedule tick (`enqueue_due`), `claim_next`, in-process stage machine. Statuses: `queued`, `running`, `succeeded`, `skipped`, `failed` | No |
| **`mlops`** | *(shared)* | Unified LLM factory (Ollama + OpenAI), VLM backends (`VLM_PROVIDER=local\|vllm`), Langfuse prompt management, traces | AI runtime infrastructure |

---

## 4. Pipeline Execution & Data Flow

```mermaid
sequenceDiagram
    autonumber
    actor Device as Planter device
    participant Orch as orchestrator.stages
    participant Cap as capture.service
    participant Assess as assessment.service
    participant Know as knowledge.interface
    participant MLOps_VLM as mlops.assessment (VLM)
    participant Adv as advice.service
    participant MLOps_Adv as mlops.advice (LLM)
    participant Comp as companion.service
    participant MLOps_Comp as mlops.companion (LLM)
    participant DB as PostgreSQL (Schemas)

    Device->>Cap: POST /capture/devices/me/frames
    Cap->>DB: vision.capture, and vision.processed_frame if quality passed

    Note over Orch: Scheduler tick queues pipeline_run (status=queued)
    Orch->>Orch: claim_next (status=running)

    Note over Orch,Cap: Stage 1: Capture (claim an already uploaded frame)
    Orch->>Cap: claim_frame(plant_id, run_id)
    alt no new processed frame since the previous run
        Cap-->>Orch: StageSkipped
    else frame claimed
        Cap->>DB: set run_id on capture and processed_frame
        Cap-->>Orch: Done
    end

    Note over Orch,Assess: Stage 2: Assessment (VLM probe, then 0-token rules)
    Orch->>Assess: assess_frames then evaluate_observation
    alt no species / fewer than two frames
        Assess-->>Orch: StageSkipped (baseline or species unset)
    else
        Assess->>Know: get_approved_checks(species_code)
        Assess->>MLOps_VLM: run_probe(checks)
        MLOps_VLM-->>Assess: ProbeVerdict
        Assess->>DB: Store observation (including probe_results_json)
        Assess->>Assess: Event Engine evaluate() + milestone detect()
        Assess->>DB: Store trigger_result + plant_milestone
        Assess-->>Orch: Done
    end

    Note over Orch,Adv: Stage 3: Advice (does not skip the run)
    Orch->>Adv: generate_for_plant(plant_id, run_id)
    Adv->>DB: Read trigger_result
    alt Decision == NO_ACTION or REQUEST_MORE_INFORMATION
        Adv-->>Orch: Return None (no advice LLM)
    else Decision == CARE_ADVICE_REQUIRED
        Adv->>Know: get_approved_checks(species_code, fired slugs)
        Adv->>MLOps_Adv: generate_advice(fired checks + basis, approved actions)
        MLOps_Adv-->>Adv: CarePlan
        Adv->>DB: care_plan, care_plan_action, diagnosis
        Adv-->>Orch: Done
    end

    Note over Orch,Comp: Stage 4: Companion (runs unless an earlier stage skipped)
    Orch->>Comp: generate_for_plant(...)
    alt NO_ACTION
        Comp->>Comp: template_steady (0 tokens)
    else REQUEST_MORE_INFORMATION
        Comp->>Comp: template_info_request (0 tokens)
    else CARE_ADVICE_REQUIRED
        Comp->>MLOps_Comp: generate_message(CarePlan, memory)
        MLOps_Comp-->>Comp: Generated message
        Comp->>Comp: fact preservation check
    end
    Comp->>DB: Backfill companion_message on assessment.observation
    Comp->>DB: Insert companion.messages
    Comp-->>Orch: Done
```

---

## 5. Database, pgvector & Alembic Architecture

### 5.1 pgvector column on knowledge chunks
* **Docker image:** `pgvector/pgvector:pg16` in `compose.yaml`.
* **PostgreSQL extension:** `CREATE EXTENSION IF NOT EXISTS vector;` in the first Alembic revision.
* **Table** (created from `knowledge.models.chunk.KnowledgeChunk`; there is no HNSW index in the migrations):
  ```sql
  CREATE TABLE knowledge.knowledge_chunks (
      id UUID PRIMARY KEY,
      species_code TEXT NOT NULL,
      topic TEXT NOT NULL,
      content TEXT NOT NULL,
      metadata_json JSONB NOT NULL,
      embedding VECTOR(1536),
      research_document_id UUID,
      probe_set_id UUID,
      source_content_hash TEXT,
      created_at TIMESTAMPTZ NOT NULL
  );
  ```

### 5.2 Alembic Multi-Schema Migrations
* **Configuration:** `alembic.ini` and `alembic/env.py` set up at the server repository root.
* **Multi-Schema Inspection:** `context.configure(..., include_schemas=True)` to track all bounded context schemas.
* **Migration order** (`alembic/versions/`):
  1. `001_init_extensions_and_schemas` (`001_init_ext_schemas`): enables `vector`, creates schemas `auth`, `registry`, `knowledge`, `action`, `orchestrator`, `assessment`, `advice`, `companion`, and creates those contexts' tables from the current models. `advice.care_plan_action` is not created here.
  2. `002_create_care_plan_action_table`: creates `advice.care_plan_action`.
  3. `003_create_vision_schema` (`003_vision_schema`): creates schema `vision` and tables `vision.capture`, `vision.processed_frame`.
  4. `004_observation_probe_results`: adds `probe_set_id` and `probe_results_json` on `assessment.observation` when missing.
  5. `005_care_plan_action_approved_source`: adds `approved_action_id`, `check_slug`, `urgency`, `expect_typical_hours`, and `expect_max_hours` on `advice.care_plan_action` when missing.
  6. `006_knowledge_chunk_provenance`: adds `research_document_id`, `probe_set_id`, and `source_content_hash` on `knowledge.knowledge_chunks`, plus a GIN index on `to_tsvector('english', content)`. No HNSW index.
* **Execution:** Run as an isolated `migrate` service in `compose.yaml` before `web` starts up:
  ```yaml
  migrate:
    build: {context: ., dockerfile: docker/Dockerfile}
    environment:
      DATABASE_URL: postgresql+psycopg://petplant:petplant@postgres:5432/petplant
    command: ["alembic", "upgrade", "head"]
    depends_on: {postgres: {condition: service_healthy}}
  ```

---

## 6. Plant registration

A signed-in user registers a plant with `POST /registry/plants` (`201`, body `PlantRead`). The required field is `name`. Optional fields are `species_code`, `species_confirmed`, `device_id`, `note`, and `owner_id`. `owner_id` is accepted only when the caller is a superuser registering a plant for someone else.

The same router lists, reads, updates, and archives plants (`GET /registry/plants`, `GET/PATCH/DELETE /registry/plants/{plant_id}`). `DELETE` sets `archived_at` and frees `device_id` for another live plant. A paired device reads the plant it photographs at `GET /registry/devices/me/plant`.

Column-level detail and the in-process interfaces are in `docs/specification.md`.
