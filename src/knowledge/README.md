# knowledge — Species & Care Knowledge (context 3)

**Package:** `src/knowledge/`

Botanical knowledge base. A person researches a species and writes what they
found as free text; that text is fed to an LLM, which turns it into **probes**
(single-question checks) and the **actions** to take when each one fires.

- **Owns (Postgres schema):** `knowledge`
- **Feeds:** `assessment` (`get_species`, and the approved probes as VLM
  checks), `advice` (the approved actions). Only **approved, structured**
  knowledge leaves this context for the pipeline; research text does not.
- **Calls out:** external LLM for probe generation (text only, never images)

Nothing here is edited in place or deleted. Revising the research text inserts a
new row and archives the one it replaces; approving another probe set archives
the set it replaces. A species therefore has exactly one **active** document and
at most one **approved** probe set, and every superseded version stays on record
— an archived probe set can be approved again to swap it back in.

## Data model

```
species
  └── research_document      human-written research text — the LLM input, kept verbatim
        └── probe_set        one LLM generation run (model, prompt version, approval, status)
              └── probe      one single-question check (care_need, question, worse/better/not_this, …)
                    └── probe_action     what to do when it fires (+ how long the effect takes)
```

| Table | Holds |
|-------|-------|
| `species` | `species_code` (`'spath'`), scientific / common name |
| `research_document` | `title`, `body` (the researched text), `content_hash`, `author`, source URL/note, `status` (`active`/`archived`), `created_at`, `archived_at` |
| `probe_set` | FK to the document, `species_code`, `source_content_hash`, `llm_model`, `prompt_version`, `langfuse_trace_id`, `status` (`draft`/`approved`/`archived`), `generated_at`, `approved_by` / `approved_at`, `rejected_by` / `rejected_at`, `archived_at`, `note` |
| `probe` | FK to the set, `slug` (unique per set), `care_need`, `priority`, `is_screening`, `question`, `worse_looks_like`, `better_looks_like`, `not_this`, `evidence_quote` |
| `probe_action` | FK to the probe, `instruction`, `urgency`, `expect_typical_hours`, `expect_max_hours` (grace period), `expected_signal` |

### Singletons enforced in the database

Two partial unique indexes carry the rule; the service layer archives the
incumbent (with `archived_at`) before promoting its replacement, so the swap is
one transaction:

```sql
CREATE UNIQUE INDEX uq_research_document_active_species
    ON knowledge.research_document (species_code) WHERE status = 'active';
CREATE UNIQUE INDEX uq_probe_set_approved_species
    ON knowledge.probe_set (species_code) WHERE status = 'approved';
```

Drafts and archived rows are unconstrained — any number may exist per species.

`probe_set.species_code` is denormalised from its research document so the index
above can be a database constraint. A composite foreign key back to
`research_document (id, species_code)` (backed by a `UNIQUE (id, species_code)`
on the parent) makes it impossible for the two to disagree.

### Keeping probes in sync with the document

`content_hash` is `SHA-256(body)`, filled in automatically on insert. Every
`probe_set` snapshots it as `source_content_hash` at generation time. The current
document for a species is its `active` row.

The `probe_set_freshness` view compares each set's snapshot against the active
document's hash and exposes `is_stale`:

```sql
SELECT * FROM knowledge.probe_set_freshness
WHERE status = 'approved' AND is_stale;   -- the approved set's source text moved on
```

It flags *when to regenerate and re-review* — it does not judge whether the
probes are correct; that is still the human `approved_by` step. Prompt/model
changes are not covered here — compare `llm_model` / `prompt_version` if you need
that too.

## Probe generation

The LLM agent lives in **`mlops.knowledge`**, not here: the prompt, its version,
its hyper-parameters and the validation of what comes back are MLOps' business.
This context owns what happens to the result — mapping it onto rows, storing it
as a **draft**, and the human review that follows.

`knowledge/generation.py` is the seam, and it has **no HTTP in it**. The same
function serves the manual endpoint today and a scheduler tomorrow:

```python
from core.db import SessionLocal
from knowledge.generation import generate_pending

with SessionLocal() as session:
    report = generate_pending(session)          # limit=…, species_code=… optional
    print(report.considered, len(report.failed))
```

**The backlog** is every `active` research document that no probe set points at
(`service.list_unconverted_documents`). Documents are append-only, so a revised
text is a new row and appears in the backlog on its own. A *rejected* draft does
**not** put its document back in the queue — a scheduled job that regenerated
after every rejection would do so forever, and a person already decided that
prompt cannot handle that text. Regenerating is a deliberate act.

**One document's failure never ends the run.** Each is attempted on its own; a
failure is recorded in `report.failed` and the rest still generate. A run that
"succeeded" with every document in `failed` is a broken prompt, not a successful
job — read the report, not the status code.

Nothing generated is ever usable before a human approves it. `generate_pending`
only ever writes `draft` rows.

## HTTP API (`/knowledge`, mounted by `main_web`)

All endpoints require an authenticated **superuser** (`core.users.CurrentSuperuser`)
— this is the authoring surface, not something plant owners call. The one
exception is `GET /knowledge/species`: any signed-in user reads it to pick a
species for a plant.

| Method & path | Purpose |
|---|---|
| `POST /knowledge/species` | register a species (409 if `species_code` is taken) |
| `GET /knowledge/species` | list registered species (**any signed-in user**) |
| `POST /knowledge/documents` | add a research document; the species' previous one is archived (404 if `species_code` is unknown) |
| `GET /knowledge/documents?species_code=&status=` | list documents, newest first; `status` is `active` or `archived` |
| `GET /knowledge/documents/pending` | the generation backlog: active documents with no probe set |
| `GET /knowledge/documents/{id}` | one document (active or archived) |
| `POST /knowledge/probe-sets/generate?species_code=&limit=` | generate a `draft` set for every document in the backlog; `limit` is 1–50 and defaults to 10 |
| `GET /knowledge/species/{species_code}/probe-sets` | every probe set for the species with `status`, `is_stale`, `probe_count` |
| `GET /knowledge/probe-sets/{id}` | one set with its probes (each carrying its `actions`) and a `trace_url` to the generation run behind it |
| `POST /knowledge/probe-sets/{id}/approve` | make this the species' approved set, archiving the incumbent (`approved_by` = caller's email); works from `draft` **and** from `archived` (swapping an older set back in); 409 if already approved or it has no probes |
| `POST /knowledge/probe-sets/{id}/reject` | turn down a `draft` with a required `comment`; 409 for any other status |
| `POST /knowledge/probe-sets/{id}/archive` | `approved` → `archived`, leaving the species with no approved set; 409 otherwise |
| `GET /knowledge/species/{species_code}/probes` | the approved bundle — same payload as the interface below |

`POST /probe-sets/generate` is a **manual trigger for what becomes a scheduled
job**, which is why it is capped: each document is an LLM call, and an unbounded
backfill behind a request would hold the connection for as long as the backlog is
deep. It answers `200` even when some documents failed — the report body says
which.

There is no `PATCH` or `DELETE` for documents: a correction is a new document,
and the superseded text stays readable as an archived row.

### Authoring flow

`register species → write research document → generate a draft probe set →
approve or reject`. `/species/{code}/probes` and the in-process interface only
ever return the **approved** set; approving another one archives the previous
automatically, and approving an archived one rolls back.

### Review is the quality signal

Nothing automatic can judge whether a probe is botanically right, answerable from
a single photograph, or guarding against the right look-alike. The superuser's
approve/reject *is* the evaluation, so it is recorded twice:

- **here**, as the `probe_set` status and its `approved_by` / `rejected_by`
  stamp, with `note` holding the reason — the record, and what the system reads;
- **in Langfuse**, as a score on the generation trace — the analytics copy, which
  is what makes "prompt v7 is accepted 80% of the time, v6 was 40%" answerable.

Only the first can fail a request. `knowledge/review.py` forwards the verdict
best-effort: a Langfuse outage never stands between a person and their decision.
A rejection **requires a comment** — the verdict alone teaches the next prompt
version nothing, and *why* it was wrong is the whole value of a human review.

## Published interface (in-process)

```python
from knowledge import get_species_probes   # (session, species_code) -> SpeciesProbesBundle | None
from knowledge.interface import get_species

bundle = get_species_probes(session, "spath")
bundle.model_dump(mode="json")   # -> {species_code, probe_set_id, status, is_stale, generated_at, probes: [...]}
```

- `get_species_probes(session, species_code)` — the species' **approved** probe
  set (with every probe's actions), or `None`. `assessment` asks
  its probes as VLM checks; `advice` builds care plans from its actions.
- `get_species(session, species_code)` — catalogue row (`SpeciesRead`) or
  `None`. `assessment.assess_frames` uses the scientific name (or the code if
  the species is unregistered).

## Internal design

```
src/knowledge/
├── api.py              # /knowledge router — argument parsing and status codes only
├── generation.py       # document → draft probe set; the seam to mlops.knowledge.
│                       #   No HTTP: an endpoint and a scheduler call the same function
├── review.py           # the human verdict → mlops.knowledge (best-effort)
├── interface.py        # get_approved_checks / get_species_probes / get_species
├── service.py          # DB reads/writes; no HTTP, no LLM, no mlops import
├── schemas.py          # Pydantic request/response + interface payloads
├── db.py               # KNOWLEDGE_SCHEMA, Base (own MetaData), JsonB, utcnow(),
│                       #   probe_set_freshness (view handle), create_views(), init_models()
├── hashing.py          # content_hash(body) -> sha256 hex
└── models/
    ├── species.py      # Species
    ├── document.py     # ResearchDocument
    ├── probe.py        # ProbeSet, Probe
    └── action.py       # ProbeAction
```

- `service.py` never imports `mlops`; `generation.py` and `review.py` are the
  only two modules that do. Persistence stays testable without the AI stack, and
  the one-way dependency (`knowledge → mlops`, never back) stays visible.
- `db.py` reuses `core.db`'s engine / pool but keeps its **own** declarative
  `Base`, so `knowledge` metadata stays isolated from the `auth` schema.
- Surrogate `uuid` primary keys everywhere except `species` (whose
  `species_code` is a real domain identifier).
- `JsonB` = `jsonb` on PostgreSQL, `json` on SQLite (local checks / tests).
- Foreign keys are declared for every parent link.
- Enum-like columns (`status`, `care_need`, `urgency`)
  are plain `text`; the partial unique indexes above are what the status values
  are actually load-bearing for.
- ORM `relationship()`s connect each parent to its children, with
  `cascade="all, delete-orphan"` down `probe_set → probe → action`
  (deleting a set removes everything under it). `species → documents` and
  `document → probe_sets` have no cascade — and nothing in the API deletes rows
  anyway; superseded rows are archived instead.
- `probe_set_freshness` is a SQL **view**, not a table; `db.py` holds a
  read-only `Table` handle for it in a separate `MetaData` so
  `Base.metadata.create_all` never tries to build it.

Until per-schema Alembic migrations land, `knowledge.db.init_models()` creates
the `knowledge` schema, its tables and the `probe_set_freshness` view directly
(used by tests; call once for a local Postgres run).
