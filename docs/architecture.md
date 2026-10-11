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
3. **Universal UUID & Timezone Timestamps:** Every primary key and reference ID across all schemas is a native PostgreSQL `UUID` (`uuid.uuid4()`). Every timestamp is a timezone-aware `TIMESTAMPTZ` (`DateTime(timezone=True)`) normalized to UTC.
4. **Deterministic Gatekeeper Pattern:** VLM assessment and milestone detection run purely algorithmic Python code (zero LLM calls, 0 tokens), protecting downstream LLMs from redundant inferences on healthy scans.
5. **pgvector Botanical Knowledge:** Species knowledge and care guides are chunked, embedded, and indexed with `pgvector` directly in PostgreSQL, enabling fast semantic similarity search for Care Advisor RAG.
6. **Alembic Multi-Schema Migrations:** All database schemas, extensions (`vector`), tables, and indexes are managed through version-controlled Alembic migrations (`include_schemas=True`).

---

## 2. Complete Entity-Relationship (ER) Diagram

```mermaid
erDiagram
    %% Auth Schema
    "auth.users" {
        UUID id PK
        TEXT email
        TEXT hashed_password
        TIMESTAMPTZ created_at
    }

    "auth.devices" {
        UUID id PK
        TEXT device_serial UK
        UUID claimed_by_user_id FK
        TIMESTAMPTZ created_at
    }

    %% Registry Schema
    "registry.plant" {
        UUID id PK
        UUID owner_id "auth.users.id"
        TEXT device_id "auth.devices.device_serial"
        TEXT name
        TEXT species_code
        INTEGER level
        REAL xp_ratio
        JSONB care_preferences
        TIMESTAMPTZ species_confirmed_at
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
        TIMESTAMPTZ archived_at
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
    "companion.message_record" {
        UUID id PK
        UUID plant_id "registry.plant.id"
        UUID run_id UK
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

    %% Knowledge Schema (pgvector)
    "knowledge.knowledge_chunks" {
        UUID id PK
        TEXT species_code
        TEXT topic
        TEXT content
        JSONB metadata
        VECTOR_1536 embedding
        TIMESTAMPTZ created_at
    }

    "knowledge.research_document" {
        UUID id PK
        TEXT species_code
        TEXT status
        TEXT markdown_body
        TEXT content_hash
        TIMESTAMPTZ created_at
    }

    %% Orchestrator Schema
    "orchestrator.pipeline_run" {
        UUID id PK
        UUID plant_id "registry.plant.id"
        TEXT status
        TEXT current_stage
        TIMESTAMPTZ created_at
        TIMESTAMPTZ completed_at
    }

    %% Logical Relationships
    "auth.users" ||--o{ "registry.plant" : "owns (owner_id)"
    "auth.users" ||--o{ "action.care_event" : "logged_by"
    "registry.plant" ||--o{ "assessment.observation" : "daily_snapshots"
    "registry.plant" ||--o{ "assessment.plant_milestone" : "life_events"
    "registry.plant" ||--o{ "advice.care_plan" : "prescribed_plans"
    "advice.care_plan" ||--o{ "advice.care_plan_action" : "contains (1:N)"
    "advice.care_plan_action" ||--o{ "action.care_event" : "completed_by (logical)"
    "registry.plant" ||--o{ "action.care_event" : "care_history"
    "registry.plant" ||--o{ "orchestrator.pipeline_run" : "runs"
    "orchestrator.pipeline_run" ||--|| "assessment.trigger_result" : "produces"
    "orchestrator.pipeline_run" ||--o| "advice.care_plan" : "generates"
    "orchestrator.pipeline_run" ||--o| "companion.message_record" : "generates"
    "knowledge.research_document" ||--o{ "knowledge.knowledge_chunks" : "chunked_into"
```

---

## 3. Bounded Context Map & Responsibilities

| Context | Schema | Primary Responsibility | LLM / Inference? |
|---|---|---|---|
| **`core`** | `auth` | User accounts, authentication, JWT tokens, hardware device provisioning | No |
| **`registry`** | `registry` | Plant profiles, species binding, nickname, location, gamification (XP, level) | No |
| **`capture`** | `capture` | MinIO image ingestion, Laplacian blur/lighting filter, crop segmentation | No |
| **`assessment`** | `assessment` | VLM consensus ingestion, deterministic Event Engine rules, milestone detection | ❌ Deterministic (0 tokens) |
| **`advice`** | `advice` | Care Advisor agent, diagnosis, CarePlan with 3NF normalized `care_plan_action` | ✅ Ollama / OpenAI via `mlops/advice/` + Langfuse |
| **`companion`** | `companion` | 1st-person plant character persona projection, Two-Tier memory, fact validator | ✅ Ollama / OpenAI via `mlops/companion/` + Langfuse |
| **`action`** | `action` | Append-only care logs (`care_event`), button click deduplication, watering timestamps | No |
| **`knowledge`** | `knowledge` | Botanical research documents, exemplar photos, **pgvector** knowledge chunk search | Embedding model for chunk vectors |
| **`orchestrator`**| `orchestrator`| Pipeline leasing (`claim_next`), stage state machine, distributed worker dispatch | No |
| **`mlops`** | *(shared)* | Unified LLM factory (Ollama + OpenAI), Langfuse prompt management, traces | AI runtime infrastructure |

---

## 4. Pipeline Execution & Data Flow

```mermaid
sequenceDiagram
    autonumber
    actor Cron as Device / Scheduler
    participant Orch as orchestrator.stages
    participant Assess as assessment.service
    participant Adv as advice.service
    participant MLOps_Adv as mlops.advice (LLM)
    participant Comp as companion.service
    participant MLOps_Comp as mlops.companion (LLM)
    participant Act as action.service
    participant DB as PostgreSQL (Schemas)

    Cron->>Orch: Start Pipeline Run (plant_id, run_id)

    %% Stage 1: Assessment
    Note over Orch,Assess: Stage 1: Assessment (Deterministic)
    Orch->>Assess: run_assessment(plant_id, run_id)
    Assess->>DB: Query latest observation & baseline
    Assess->>Assess: Event Engine evaluate() + Milestone detect()
    Assess->>DB: Store TriggerResult + PlantMilestones (assessment schema)
    Assess-->>Orch: Done

    %% Stage 2: Advice
    Note over Orch,Adv: Stage 2: Advice (Conditional)
    Orch->>Adv: run_advice(plant_id, run_id)
    Adv->>DB: Read TriggerResult
    alt Decision == NO_ACTION or REQUEST_MORE_INFORMATION
        Adv-->>Orch: Return None (Skip Advice LLM)
    else Decision == CARE_ADVICE_REQUIRED
        Adv->>DB: Query 5-day observation history
        Adv->>DB: pgvector cosine search in knowledge.knowledge_chunks
        Adv->>MLOps_Adv: generate_advice(observations, RAG_context)
        MLOps_Adv-->>Adv: CarePlan (status_label, actions_json)
        Adv->>DB: Store CarePlan (advice schema)
        Adv-->>Orch: Done
    end

    %% Stage 3: Companion
    Note over Orch,Comp: Stage 3: Companion (Always Runs)
    Orch->>Comp: run_companion(plant_id, run_id)
    Comp->>DB: Read TriggerResult & CarePlan (if exists)
    Comp->>DB: Read 7-day observations (Tier 1 memory)
    Comp->>DB: Read plant_milestones (Tier 2 memory)
    alt Decision != CARE_ADVICE_REQUIRED
        Comp->>Comp: Select static template (0 tokens)
    else Decision == CARE_ADVICE_REQUIRED
        Comp->>MLOps_Comp: generate_message(CarePlan, Memory)
        MLOps_Comp-->>Comp: Generated message
        Comp->>Comp: validator.py (fact preservation check >=34%)
    end
    Comp->>DB: Backfill companion_message in assessment.observation
    Comp->>DB: Store message_record (companion schema)
    Comp-->>Orch: Done
```

---

## 5. Database, pgvector & Alembic Architecture

### 5.1 pgvector Vector Store for Knowledge RAG
* **Docker Image:** `pgvector/pgvector:pg16-alpine` replaces standard postgres in `compose.yaml`.
* **PostgreSQL Extension:** `CREATE EXTENSION IF NOT EXISTS vector;` enabled via Alembic initial revision.
* **Knowledge Chunks Table:**
  ```sql
  CREATE TABLE knowledge.knowledge_chunks (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      species_code TEXT NOT NULL,
      topic TEXT NOT NULL,
      content TEXT NOT NULL,
      metadata JSONB NOT NULL DEFAULT '{}',
      embedding VECTOR(1536),
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  );
  CREATE INDEX idx_knowledge_chunks_embedding
      ON knowledge.knowledge_chunks
      USING hnsw (embedding vector_cosine_ops);
  ```

### 5.2 Alembic Multi-Schema Migrations
* **Configuration:** `alembic.ini` and `alembic/env.py` set up at the server repository root.
* **Multi-Schema Inspection:** `context.configure(..., include_schemas=True)` to track all bounded context schemas.
* **Migration Order:**
  1. `001_init_extensions_and_schemas`: Enables `vector`, creates schemas `auth`, `registry`, `action`, `knowledge`, `orchestrator`, `assessment`, `advice`, `companion`.
  2. `002_create_tables_and_indexes`: Creates all tables according to the ER diagram with `UUID` PKs, `TIMESTAMPTZ`, `JSONB`, and `VECTOR`.
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

## 6. Post-Implementation Technical Specification Requirement

> [!IMPORTANT]
> **Post-Implementation Deliverable ("Spec after all the code is done"):**
> Upon completion of the implementation phases (assessment, advice, companion, pgvector knowledge RAG, orchestrator wiring), a finalized **Technical & Interface Specification document** (`docs/interface-specification.md`) must be generated/updated for the server repo.
>
> This document will freeze and detail:
> 1. Exact HTTP REST route contracts (request/response schemas, error status codes) for `GET /companion/devices/me/state` and management endpoints.
> 2. Final database column dictionary with actual constraints and foreign table linkages.
> 3. Langfuse prompt versions and evaluation criteria.
> 4. Verification test matrix results and end-to-end trace proofs.
