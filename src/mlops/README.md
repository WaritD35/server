# `mlops` — LLM execution and experiments

Every VLM / LLM call in the system goes through here, and so does every
experiment behind those calls. **Langfuse** manages the prompts.

> Status: `mlops/knowledge/` is **implemented** — the probe-generation agent, its
> validation, its human-review score and its experiment runner. `mlops/assessment/`
> is **implemented** — `run_probe`, local|vllm backends, and aggregation.
> `assessment/`, `advice/` and `companion/` register their prompts with
> `python -m mlops.<component>.seed`; `advice/` and `companion/` resolve their
> prompts from Langfuse, but their experiment runners are not written yet. Read
> `knowledge/` as the worked example of the Langfuse shape; `assessment/` is the
> VLM counterpart.

## The stack

Pinned in `pyproject.toml` because it lives here and nowhere else:

| Package | For |
|---|---|
| `langfuse` | Prompt management, tracing, datasets, scores. Ships a LangChain callback handler, so traces come off the chain rather than being hand-rolled |
| `langchain` | Chain / agent construction. v1 bundles `langgraph`, so a component that needs a graph already has one |
| `langchain-openai` | The OpenAI-compatible integration — reaches both the on-prem VLM (vLLM, `VLM_API_BASE`) and the external text LLM (`LLM_API_BASE`) |

Both model endpoints speak the OpenAI protocol, so they differ only by base URL,
key and model id — one `ChatOpenAI` shape covers both.

## Not a bounded context

`mlops` owns no Postgres schema and no HTTP router. It is a shared library
alongside `core` that the LLM-using contexts depend on:

```
assessment ─┐
advice     ─┼─▶ mlops ─┬─▶ Langfuse  (prompts / traces / datasets / scores)
knowledge  ─┤          └─▶ VLM (on-prem, vLLM) · LLM (external, text only)
companion  ─┘
```

**The dependency is one-way.** `mlops` never imports a bounded context — its
entry points take plain values and dataclasses defined here, and the calling
context maps its own models onto them (e.g. `assessment` builds an
`mlops.assessment.ProbeInput` with `plant_id`, `species`, `previous_image`,
`current_image`, and the approved `checks`). Without this rule
`knowledge → mlops → knowledge` closes a cycle.

## One package per component, one owner each

Each component is **its own Langfuse project** with its own key pair, and each
package is **self-contained**: it decides how it handles prompts, calls its
model, traces and scores. There is deliberately no shared abstraction layer over
Langfuse — the four owners can work in parallel without agreeing on one, and
without a change to shared code rippling across all four.

| Package | Model | Entry point the context calls | Owner |
|---|---|---|---|
| `mlops/assessment/` | VLM (`VLM_PROVIDER=local` or `vllm`) | `run_probe(ProbeInput) -> ProbeVerdict` | implemented |
| `mlops/advice/` | LLM (`LLM_PROVIDER`) | `generate_advice(AdviceInput) -> AdviceResult` | prompt in Langfuse; experiment TODO |
| `mlops/knowledge/` | LLM | `generate_probes(GenerateProbesInput) -> GenerateProbesResult` | TODO |
| `mlops/companion/` | LLM (`LLM_PROVIDER`) | `generate_companion_message(...) -> str \| None` | prompt in Langfuse; no experiment yet |

### The template inside each package

| File | Holds |
|---|---|
| `prompts.py` | The prompt names in that Langfuse project, and how to fetch a version. One place, so `runtime` and `experiment` agree |
| `runtime.py` | **Execution code.** Resolve the `production` prompt → call the model → parse → trace. Takes `label` / `version` / `overrides` so experiments can drive it |
| `experiment.py` | **Experiment code.** Runs `runtime`'s function over a Langfuse dataset as a dataset run. `python -m mlops.<component>.experiment` |
| `evaluators.py` | That component's scorers |

Add modules beyond these as the work needs them — `knowledge/` added `schemas.py`
(the output contract) and `review.py` (the human verdict). That is the owner's
call.

### Validate before you return

`knowledge/` sets the pattern the others should follow: the output model is
Pydantic, it is handed to the LLM as a JSON schema through
`with_structured_output`, and it is validated again on the way back. A failure
becomes a **repair message** — the model sees its own answer's errors and fixes
them — and a set that never validates raises rather than returning. There is no
"mostly valid" path out of a runtime module.

Three layers, each catching what the one before it cannot:

| Layer | Catches |
|---|---|
| JSON schema in the request | Missing fields, wrong types |
| Pydantic validators | Domain rules: slug format, unique identifiers, ordered durations |
| A grounding check against the source | Hallucination — output that cites text the input does not contain |

**`experiment.py` calls `runtime.py`'s own function**, with a pinned prompt
version rather than the `production` label. Experiments and production therefore
share one code path, so a prompt that scored well cannot behave differently once
its label moves. This is why every `runtime` entry point takes `label` /
`version` / `overrides` — keep that shape.

## What is shared (and it is only this)

| Module | Holds |
|---|---|
| `settings.py` | `Component(StrEnum)` — subpackage, Langfuse project and key pair keyed by one value — plus `PRODUCTION_LABEL` and `credentials_for()` |
| `client.py` | `get_client(component)` — a Langfuse client pointed at the right project. Credential plumbing, not an abstraction over the SDK |

## How prompts are managed

Everything an in-house prompt registry would carry lives in Langfuse instead:

| Concern | Where it lives |
|---|---|
| Prompt text | Langfuse prompt version |
| Hyper-parameters (model, temperature, …) | That version's `config` JSON |
| Version identity | Langfuse version number |
| What is live | The `production` label |
| Promotion / rollback | Moving that label — old versions are never deleted |
| Eval set / run / scores | Langfuse Dataset / Dataset Run / Scores |

The request path resolves by **label**; experiments pin a **version**.

## Evaluation: what a machine can score, and what it cannot

Not every component has an automatic quality metric, and pretending otherwise is
worse than admitting it — a scorer nobody believes still moves a `production`
label. `knowledge` is the clear case: whether a generated probe is botanically
right is a human judgement, so it is scored by a human.

| | Automatic | Human |
|---|---|---|
| What it measures | Well-formedness: did it validate, how many repair rounds, coverage | Quality: is this right, is it usable |
| Where it runs | `evaluators.py`, every dataset run | A person reviewing, in the authoring UI or a Langfuse annotation queue |
| What it gates | Whether a version is a candidate at all | Whether it ships |

For `knowledge`, the review surface is the one people already use: a superuser
approving or rejecting a draft probe set in the `knowledge` API writes the same
Langfuse score (`probe_set_human_review`) that an annotation queue would. The
verdict is durable in Postgres first; the copy in Langfuse is best-effort and
never fails the reviewer's request. Over time it answers the question that
matters — *what fraction of what prompt v7 produced did people accept?*

**No experiment data is written to Postgres.** If a context later needs to
correlate its own rows with a trace, the minimal move is one `langfuse_trace_id`
column on that context's table — every `runtime` result carries the id for
exactly this.

## Config

`LANGFUSE_HOST` plus a public/secret key pair per component, declared in
`core.config.Settings` and `.env.example`. `VLM_*` / `LLM_*` supply the model
endpoints and their credentials.

**No model name lives in the environment.** Which model a prompt runs on is part
of that prompt version's `config`, so it is versioned with the text it was tuned
against, and the model a past run used is recoverable from the version it names.
An env-level default would quietly reintroduce a second source: a prompt missing
`model` would still run, on whatever the deployment happened to say, and two runs
of "v7" could differ by machine — which is exactly what makes comparing versions
meaningless. A prompt config with no `model` raises instead.

### What `knowledge` needs in Langfuse

A chat prompt named **`knowledge/generate-probes`** with the `production` label,
compiled with exactly these variables (`prompts.GENERATE_PROBES_VARIABLES`):

`species_code`, `scientific_name`, `common_name`, `document_title`, `document_body`

Its `config` carries the hyper-parameters — `model`, `temperature`, `max_tokens`,
`max_attempts` (the repair budget), and anything else is passed through to the
client. A dataset named `knowledge-research-documents` backs the experiment
runner; its items have no `expected_output`, because nobody can write the one
correct probe set for a document.

Both are bootstrapped by `scripts/seed_langfuse_knowledge.py` (`--dry-run` prints
the prompt without sending it). That script is a **starting point, not the source
of truth**: after the first run the prompt is edited and versioned in the Langfuse
UI, and running the script again creates another version rather than updating v1.

### What `assessment` needs in Langfuse

A **text** prompt named **`assessment/plant_observation_pair`** with the
`production` label, compiled with exactly
`PROBE_VARIABLES = ("probe_checks",)`. `probe_checks` is
the species' **approved `knowledge` probes** (one block per check: slug,
question, what "yes" and "no" look like, and what it is not), or `""`.
No free research text is sent. Older versions still holding the retired
`{{plant_knowledge}}` or `{{retrieved_passages}}` have them compiled to `""`
(`prompts.RETIRED_VARIABLES`). The runtime
sends the compiled text as the text part of a single user message next to the
two images.

The answer carries one `probe_findings` entry per listed check (`slug`,
`answer` = `yes | no | cannot_tell`, `evidence`). The extracted JSON object is
checked against the Draft 2020-12 schema of `RunObservation`
(`mlops.assessment.schemas.OBSERVATION_JSON_SCHEMA`) and then validated as that
model. A schema or model failure, or an unknown or duplicated slug, sends the
run back for its one repair; a missing finding counts as `cannot_tell`. Each
check is voted on over all valid runs like the health status (ties lean to
`yes`) and comes back in `ProbeVerdict.probe_results`. A version without
`{{probe_checks}}` still runs, but its checks are not asked or recorded (a
warning is logged) — register the new bootstrap text with the seed CLI to
start asking them. When checks are present, a run with any `yes` cannot be
healthy; a run where every check is `no` is healthy; missing or unclear answers
remain cautious and defer to the visible-stress assessment. Without checks,
the prior visible-stress behavior is unchanged.

Its `config` **must** carry `runs`, `temperature`, `max_new_tokens` and `model`.
A missing key, `runs` outside `1..8`, or `max_new_tokens` outside `1..2048` raises
`AssessmentUnavailableError` (callers map that to 503); there are no runtime
defaults and no model name in the environment.

Register it with `python -m mlops.assessment.seed --model <served model id>`
(see [Registering a new prompt version](#registering-a-new-prompt-version)). The
text and config it sends are `prompts.BOOTSTRAP_PROMPT` / `BOOTSTRAP_CONFIG`; the
runtime never reads them, so a missing Langfuse prompt still raises.

### What `advice` needs in Langfuse

A **chat** prompt named **`advice/diagnose`** with the `production` label,
compiled with exactly `prompts.DIAGNOSE_VARIABLES`:

`plant_id`, `health_status`, `trigger_reason`, `symptoms`, `observation_history`,
`fired_checks`, `approved_actions`

(`symptoms` and `observation_history` arrive as JSON strings.) **Only approved,
structured knowledge** is sent, fetched by `advice` with
`knowledge.get_approved_checks(species, fired slugs)`: each fired check with
what was seen, its approved basis (`evidence_quote`) and what it is not
(`fired_checks`), and the approved `knowledge` probe actions the plan may use,
listed as `A1`, `A2`, … (those of the fired checks; every approved action of the
species when none fired). A production version that declares variables but not
`{{fired_checks}}` and `{{approved_actions}}` is not run: the bootstrap prompt is
used with a warning, until a new version is seeded and promoted. The model does not write instructions:
`GeneratedCarePlan` names an
approved action per step (`approved_action_id`), its priority, a button label and
a type. An id that was not listed sends the answer back for one repair; the
instruction, urgency and expected time to effect are copied from the approved
action, so every step traces to one.

Its `config` carries `temperature` and, optionally, `model`. Advice is the one
component where `model` may be absent: it reaches its model through
`mlops.factory.get_llm`, where the deployment picks the provider
(`LLM_PROVIDER=openai|ollama`) and a model id only means something to one of
them. A version that names `model` pins it; one that does not runs on the
provider's default.

Advice must not stop the pipeline, so it **degrades** instead of raising: if the
`production` prompt cannot be fetched it runs the in-repo bootstrap prompt
(`prompts.BOOTSTRAP_MESSAGES`), and if the LLM call fails it returns the
approved actions in order. A plant with **no approved actions** gets no LLM call
at all, only a generic "inspect the plant" step: without approved knowledge the
system does not prescribe. `AdviceResult.prompt_ref` (`advice/diagnose@3`, or
`…@bootstrap`) and `AdviceResult.source` (`llm` | `fallback` | `no_knowledge`)
say which path produced a plan. A pinned `version` (an experiment) never falls
back.

Register it with `python -m mlops.advice.seed [--model <id>]`.

### What `companion` needs in Langfuse

A **chat** prompt named **`companion/speak`** with the `production` label,
compiled with exactly `prompts.SPEAK_VARIABLES`:

`plant_nickname`, `assessment`, `health_status`, `tone`, `memory`, `actions`

`memory` is the whole optional history section (7-day observations and
milestones) or `""`; `actions` is one `- ` line per care action.

Its `config` carries `temperature`, optionally `model` (as for `advice`),
`max_tokens` (default 120, so the spoken reply stays short), and the plant's
voice: `tones` maps a `health_status` to the text compiled into `{{tone}}`, and
`default_tone` covers any other status. A version without tones uses the
bootstrap tones. A reply longer than 4 sentences or 480 characters is cut at a
sentence boundary before it is returned.

Like advice, the companion degrades: the bootstrap prompt stands in when the
`production` prompt cannot be fetched, and on an LLM failure it returns `None`,
so `src/companion` uses its deterministic template. A pinned `version` never
falls back.

Register it with `python -m mlops.companion.seed [--model <id>]`.

### Registering a new prompt version

`python -m mlops.<component>.seed` (assessment, advice and companion) registers the in-repo
bootstrap prompt as a Langfuse version. Langfuse numbers the versions and nothing
is overwritten:

| State in Langfuse | What `seed` does |
|---|---|
| The prompt does not exist | Creates v1 **with** `production` — the runtime needs a live version |
| The latest version has the same text and config | Nothing |
| Anything else | Creates a new version **without** `production` (`--promote` labels it) |

`--check` verifies the key pair, `--dry-run` prints what would be sent. The
bootstrap text is a **starting point, not the source of truth**: day-to-day edits
happen in the Langfuse UI. Edit the in-repo text when the change should be
reviewed in a PR (e.g. it goes with a code change to the variables or the output
schema), then run `seed` and promote the new version once it has been tried.

### Notes (Tier 2)

- `VLM_PROVIDER=local` runs the configured number of inferences (typically 5)
  in-process; on CPU this can take minutes. Development only; production uses
  `vllm`.
- Assessment requires Langfuse credentials and the prompt above. There is no
  runtime prompt fallback.

## Bringing up a component's Langfuse project

Nothing here works until the project exists and its keys are in `.env`. Only the
first step is manual — Langfuse Cloud has no API for creating a project, so the
project and its key pair are made in the web UI. (Self-hosted Langfuse can do
this headlessly with its `LANGFUSE_INIT_*` variables at container start; see the
Langfuse self-hosting docs.)

1. **In the Langfuse UI**, create a project named after the component
   (`knowledge`), then *Settings → API keys* → create one. One project per
   component is the whole design — sharing one project across components mixes
   their traces and loses the per-component accept-rate.
2. **In `.env`**, set `LANGFUSE_HOST` to your region and the component's key
   pair. The SDK's own `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` /
   `LANGFUSE_BASE_URL` are **not** read by this project; the per-component names
   in `.env.example` are.
3. **Verify** before doing anything else:
   `uv run python scripts/seed_langfuse_knowledge.py --check`
4. **Seed** the prompt and the experiment dataset:
   `uv run python scripts/seed_langfuse_knowledge.py`
5. **Run an experiment:**
   `uv run python -m mlops.knowledge.experiment --run-name v1-baseline`

### Experiments need no database

Steps 3–5 touch Postgres, MinIO and the web process not at all — prompts,
datasets, runs, traces and scores all live in Langfuse. Two API keys and a laptop
are the whole requirement, with nothing from `compose.yaml` running.

That is a property worth keeping, and it is easy to break with one convenience
import: `tests/mlops/test_no_database.py` fails if importing the experiment CLI
ever pulls in `core.db`, SQLAlchemy or a bounded context.

## Adding a component

1. Add a member to `Component` in `settings.py`.
2. Add its key pair to `core.config.Settings` and `.env.example`.
3. Register it in `settings.credentials_for()`.
4. Copy an existing component package as the template.
5. Create its Langfuse project and keys, as above.
