# 5. Care Advice

**Package:** `src/advice/`
**Role:** Interpretable reasoning & action — turn verdicts plus history into
specific, ordered care actions with their reasoning.
**Owner:** _TBD_

## Responsibilities

- Analyse the current observations and this plant's history to produce a specific
  **diagnosis**.
- Recommend ordered steps taken **only from approved `knowledge` actions** — the
  actions of the approved checks that fired on the run (every approved action of
  the species when none fired). The LLM orders and labels them; the instruction,
  urgency and expected time to effect are the approved ones, and each stored
  step records the approved action it came from. A plant with no approved
  actions gets a generic "inspect the plant" step, never invented advice.
- Turn an abstaining probe into a question to the owner rather than a guess;
  escalate when prior advice went unheeded.
- Produce the character's words for `companion`.

## Data it owns

- PostgreSQL schema `advice`: diagnoses, actions, rationale, citations, advice
  history (what was advised, when, whether the owner marked it done).
- MinIO: none.

## Inputs

- **Internal state & verdicts** from `assessment`.
- **Approved** checks and actions from `knowledge.get_species_probes`, and the fired checks from the run's observation. Care steps come only from those actions.
- Passages from `knowledge.search_approved_passages`, retrieved when approved actions exist. They inform the assessment. Approving a probe set is what stores the research document for that search.
- External LLM — **text only, never images**.

## Outputs

- **Diagnosis & actions** (+ character line) → `companion`.

## Driven by

- `orchestrator` events / `main_worker` (advise for a completed assessment cycle).

## HTTP surface

This context owns `src/advice/api.py` exposing `router: APIRouter`, plus its own
request/response schemas (organised however the owner prefers). `main_web` mounts
the router; routes apply the auth dependencies provided by `core`.

## Internal design

Left to the context owner. Other contexts use only this context's published
interface; no access to the `advice` schema from outside.
