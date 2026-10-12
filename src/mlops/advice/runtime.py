"""Execution code for ``advice`` — diagnosis and a ranked care plan.

Owner: TODO

``src/advice`` calls :func:`generate_advice` from the pipeline's advice stage:
resolve the ``production`` prompt, call the LLM with structured output, trace it
into the advice Langfuse project.

Inputs are plain values — ``mlops`` must not import a bounded context, so the
calling context flattens its own models onto :class:`AdviceInput`.

## Only approved knowledge

The only care knowledge the model sees is **human-approved and structured**:
the species checks assessment found firing (:class:`FiredCheck`) and the approved
actions behind them (:class:`ApprovedAction`, a ``knowledge`` probe action).
Free research text is never sent.

The model does not write instructions. It picks approved actions by their
listed id, orders them, and gives each a short button label and a type; the
instruction, urgency and expected time to effect are copied from the approved
action. An answer that names an id that was not listed goes back for one
repair, so every action in a plan traces to an approved one.

## Degrading, not failing

Advice runs between assessment and companion, and the companion stage must
still run, so this function always returns a plan:

```
no approved action for the plant ─────────────────────────▶ generic inspection plan
                                                              (source = "no_knowledge")
Langfuse chat prompt (production label) ──(unavailable)──▶ bootstrap prompt
      │  compiled with AdviceInput                          (prompts.BOOTSTRAP_*)
      ▼
LLM.with_structured_output(GeneratedCarePlan) ──(fails)──▶ approved actions in order
                                                              (source = "fallback")
```

An experiment pins ``version`` and gets **no** prompt fallback — a run scored
against the wrong prompt is worse than no run.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from langfuse import propagate_attributes
from pydantic import BaseModel, Field, ValidationError, field_validator

from mlops.advice import prompts
from mlops.client import get_callback_handler, get_client, is_configured
from mlops.settings import PRODUCTION_LABEL, Component

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from langfuse.model import ChatPromptClient

logger = logging.getLogger(__name__)

COMPONENT: Final = Component.ADVICE

ACTION_TYPES: Final[frozenset[str]] = frozenset({"water", "move", "inspect", "other"})

#: Attempts at a valid plan, including the first; each retry carries the errors.
MAX_ATTEMPTS: Final[int] = 2

#: Most approved actions the fallback plan carries when the model fails.
FALLBACK_ACTIONS: Final[int] = 3


# --------------------------------------------------------------------------- #
# input
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FiredCheck:
    """An approved species check that assessment found firing on this run."""

    slug: str
    care_need: str
    question: str
    evidence: str = ""


@dataclass(frozen=True)
class ApprovedAction:
    """One human-approved care action (a ``knowledge`` probe action), flattened."""

    #: ``knowledge.probe_action.id``.
    id: str
    #: The check it belongs to.
    check_slug: str
    care_need: str
    instruction: str
    urgency: str
    expect_typical_hours: int
    expect_max_hours: int
    expected_signal: str | None = None


@dataclass(frozen=True)
class AdviceInput:
    """What ``src/advice`` passes in."""

    plant_id: str
    health_status: str
    symptoms: list[dict[str, Any]] = field(default_factory=list)
    observation_history: list[dict[str, Any]] = field(default_factory=list)
    trigger_reason: str | None = None
    confidence_agreement: float | None = None
    #: The approved checks that fired on this run, in the order they were asked.
    fired_checks: tuple[FiredCheck, ...] = ()
    #: The approved actions the plan may use: those of the fired checks, or every
    #: approved action of the species when none fired. Empty → no LLM call.
    approved_actions: tuple[ApprovedAction, ...] = ()
    #: The orchestrator run, for tracing only (Langfuse trace metadata).
    run_id: str | None = None

    def trace_metadata(self) -> dict[str, str]:
        """Searchable trace metadata: ``plant_id`` and, when known, ``run_id``."""
        metadata = {"plant_id": self.plant_id}
        if self.run_id:
            metadata["run_id"] = self.run_id
        return metadata

    def action_keys(self) -> dict[str, ApprovedAction]:
        """``A1``, ``A2``, … → approved action: the ids the model refers to.

        Short keys rather than UUIDs, so a small model copies them reliably.
        """
        return {f"A{i}": action for i, action in enumerate(self.approved_actions, start=1)}

    def as_prompt_variables(self) -> dict[str, str]:
        """The values the Langfuse template is compiled with.

        Keys are exactly :data:`~mlops.advice.prompts.DIAGNOSE_VARIABLES`.
        """
        checks = (
            "\n".join(
                f"- {c.slug} ({c.care_need}): {c.question}"
                + (f" Seen: {c.evidence}" if c.evidence else "")
                for c in self.fired_checks
            )
            or "None of the approved checks fired."
        )
        actions = "\n".join(
            f"- {key}: {a.instruction} (check: {a.check_slug}, urgency: {a.urgency}, "
            f"effect expected within {a.expect_typical_hours}-{a.expect_max_hours} h)"
            for key, a in self.action_keys().items()
        )
        return {
            "plant_id": self.plant_id,
            "health_status": self.health_status,
            "trigger_reason": self.trigger_reason or "Health change",
            "symptoms": json.dumps(self.symptoms),
            "observation_history": json.dumps(self.observation_history),
            "fired_checks": checks,
            "approved_actions": actions,
        }


# --------------------------------------------------------------------------- #
# model output
# --------------------------------------------------------------------------- #


class GeneratedCareAction(BaseModel):
    """One step of the plan: a reference to an approved action, not new text."""

    #: The listed key (``A1``, ``A2``, …) of the approved action.
    approved_action_id: str = Field(min_length=1)
    #: 1 = do first.
    priority: int = Field(ge=1)
    #: Two or three words for the button, e.g. "Pause water".
    label: str = Field(min_length=1, max_length=40)
    type: str = "other"

    @field_validator("approved_action_id", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("type", mode="before")
    @classmethod
    def _known_type(cls, value: Any) -> Any:
        normalized = value.strip().lower() if isinstance(value, str) else value
        return normalized if normalized in ACTION_TYPES else "other"


class GeneratedCarePlan(BaseModel):
    status_label: str = Field(min_length=1)
    assessment: str = Field(min_length=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    actions: list[GeneratedCareAction] = Field(min_length=1)

    def check_against(self, keys: set[str]) -> None:
        """Every action must name a listed approved action, each at most once."""
        seen: set[str] = set()
        for action in self.actions:
            if action.approved_action_id not in keys:
                raise ValueError(
                    f"approved_action_id {action.approved_action_id!r} is not one of "
                    f"{sorted(keys)}"
                )
            if action.approved_action_id in seen:
                raise ValueError(f"approved action {action.approved_action_id!r} used twice")
            seen.add(action.approved_action_id)


# --------------------------------------------------------------------------- #
# result
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AdviceResult:
    """What comes back to the advice service layer."""

    care_plan_id: str
    status_label: str
    assessment: str
    confidence: float
    #: Each: ``id``, ``priority``, ``action`` (the approved instruction),
    #: ``label``, ``type`` and, unless ``source`` is ``no_knowledge``,
    #: ``approved_action_id``, ``check_slug``, ``care_need``, ``urgency``,
    #: ``expect_typical_hours``, ``expect_max_hours``.
    actions: list[dict[str, Any]]
    trace_id: str | None = None
    #: ``llm`` — the model ordered approved actions; ``fallback`` — the model
    #: failed and the approved actions were used in order; ``no_knowledge`` — the
    #: plant has no approved action, so a generic inspection plan was returned.
    source: str = "llm"
    prompt_name: str = prompts.DIAGNOSE_PROMPT
    #: The Langfuse version used; ``None`` when the bootstrap prompt stood in.
    prompt_version: int | None = None

    @property
    def prompt_ref(self) -> str:
        """``advice/diagnose@3``, or ``advice/diagnose@bootstrap``."""
        return f"{self.prompt_name}@{self.prompt_version or 'bootstrap'}"


@dataclass(frozen=True)
class AdviceConfig:
    """Model hyper-parameters, read from the Langfuse prompt version's ``config``.

    ``model`` is optional here, unlike ``knowledge`` and ``assessment``: advice
    reaches its model through :func:`mlops.factory.get_llm`, where the deployment
    picks the provider (``LLM_PROVIDER=openai|ollama``) and a model id only means
    something to one of them. A version that names ``model`` pins it; one that
    does not runs on the provider's default model.
    """

    model: str | None = None
    temperature: float = 0.2

    @classmethod
    def from_prompt_config(
        cls, config: Any, *, overrides: dict[str, Any] | None = None
    ) -> AdviceConfig:
        raw: dict[str, Any] = dict(config) if isinstance(config, dict) else {}
        raw.update(overrides or {})
        unknown = set(raw) - {"model", "temperature"}
        if unknown:
            logger.warning("ignoring unknown advice prompt config key(s): %s", sorted(unknown))
        model = str(raw.get("model") or "").strip() or None
        return cls(model=model, temperature=float(raw.get("temperature", 0.2)))


def _new_action_id() -> str:
    return f"act_{uuid.uuid4().hex[:8]}"


def _approved_step(
    action: ApprovedAction, *, priority: int, label: str, action_type: str
) -> dict[str, Any]:
    return {
        "id": _new_action_id(),
        "priority": priority,
        "action": action.instruction,
        "label": label,
        "type": action_type,
        "approved_action_id": action.id,
        "check_slug": action.check_slug,
        "care_need": action.care_need,
        "urgency": action.urgency,
        "expect_typical_hours": action.expect_typical_hours,
        "expect_max_hours": action.expect_max_hours,
    }


def _plan_from_model(payload: AdviceInput, output: GeneratedCarePlan) -> list[dict[str, Any]]:
    keys = payload.action_keys()
    ranked = sorted(output.actions, key=lambda a: a.priority)
    return [
        _approved_step(
            keys[a.approved_action_id], priority=rank, label=a.label, action_type=a.type
        )
        for rank, a in enumerate(ranked, start=1)
    ]


def _approved_fallback(payload: AdviceInput) -> tuple[str, str, list[dict[str, Any]]]:
    """The model failed: the first approved actions, as approved, in order."""
    chosen = payload.approved_actions[:FALLBACK_ACTIONS]
    needs = list(dict.fromkeys(a.care_need for a in chosen))
    status = f"Care needed: {needs[0].replace('_', ' ')}" if needs else "Care needed"
    fired = ", ".join(c.slug for c in payload.fired_checks)
    assessment = (
        f"Approved checks fired: {fired}." if fired else (payload.trigger_reason or status)
    )
    return (
        status,
        assessment,
        [
            _approved_step(
                a,
                priority=rank,
                label=a.care_need.replace("_", " ").capitalize()[:40],
                action_type="other",
            )
            for rank, a in enumerate(chosen, start=1)
        ],
    )


def _no_knowledge_plan(payload: AdviceInput) -> tuple[str, str, list[dict[str, Any]]]:
    """No approved action exists for the plant: ask the owner to look, nothing more.

    Not care advice — without approved knowledge the system does not prescribe.
    """
    symptom_types = [s.get("type", "symptom") for s in payload.symptoms if isinstance(s, dict)]
    symptom_str = ", ".join(symptom_types) if symptom_types else "a change"
    return (
        f"{payload.health_status.replace('_', ' ').capitalize()}: {symptom_str}",
        (
            f"Observation detected {symptom_str} with {payload.health_status} status. "
            "No approved care actions exist for this plant yet."
        ),
        [
            {
                "id": _new_action_id(),
                "priority": 1,
                "action": "Take a close look at the leaves and soil and note what has changed.",
                "label": "Inspect plant",
                "type": "inspect",
            }
        ],
    )


def _resolve_prompt(label: str | None, version: int | None) -> ChatPromptClient | None:
    """The Langfuse version to run, or ``None`` to use the bootstrap prompt.

    Only the request path (no pinned ``version``) may fall back.
    """
    if version is not None:
        return prompts.get(prompts.DIAGNOSE_PROMPT, label=label, version=version)
    if not is_configured(COMPONENT):
        logger.debug("advice Langfuse project not configured; using the bootstrap prompt")
        return None
    try:
        return prompts.get(prompts.DIAGNOSE_PROMPT, label=label)
    except Exception:
        logger.warning(
            "cannot fetch Langfuse prompt %r; using the bootstrap prompt",
            prompts.DIAGNOSE_PROMPT,
            exc_info=True,
        )
        return None


def generate_advice(
    payload: AdviceInput,
    *,
    label: str | None = PRODUCTION_LABEL,
    version: int | None = None,
    overrides: dict[str, Any] | None = None,
) -> AdviceResult:
    """Produce a diagnosis and a plan built only from approved actions.

    ``label`` / ``version`` / ``overrides`` exist so ``experiment.py`` can pin a
    prompt version and drive this exact function — experiments and production
    then share one code path. The request path passes none of them.
    """
    care_plan_id = f"cp_{uuid.uuid4().hex[:12]}"
    if not payload.approved_actions:
        logger.info("no approved care actions for plant %s; generic plan", payload.plant_id)
        status, assessment, actions = _no_knowledge_plan(payload)
        return AdviceResult(
            care_plan_id=care_plan_id,
            status_label=status,
            assessment=assessment,
            confidence=0.0,
            actions=actions,
            source="no_knowledge",
        )

    prompt = _resolve_prompt(label, version)
    variables = payload.as_prompt_variables()
    if prompt is not None:
        messages = prompt.compile(**variables)
        config = AdviceConfig.from_prompt_config(prompt.config, overrides=overrides)
    else:
        messages = prompts.compile_bootstrap(variables)
        config = AdviceConfig.from_prompt_config(prompts.BOOTSTRAP_CONFIG, overrides=overrides)

    trace_id: str | None = None
    try:
        output, trace_id = _invoke(messages, config, prompt, payload)
        status, assessment, confidence = output.status_label, output.assessment, output.confidence
        actions = _plan_from_model(payload, output)
        source = "llm"
    except Exception:
        logger.warning("advice LLM call failed; using the approved actions in order", exc_info=True)
        status, assessment, actions = _approved_fallback(payload)
        confidence = 0.5
        source = "fallback"

    return AdviceResult(
        care_plan_id=care_plan_id,
        status_label=status,
        assessment=assessment,
        confidence=confidence,
        actions=actions,
        trace_id=trace_id,
        source=source,
        prompt_name=prompt.name if prompt is not None else prompts.DIAGNOSE_PROMPT,
        prompt_version=prompt.version if prompt is not None else None,
    )


def _repair_message(error: Exception) -> tuple[str, str]:
    if isinstance(error, ValidationError):
        detail = json.dumps(
            [
                {"field": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]}
                for e in error.errors()
            ],
            ensure_ascii=False,
        )
    else:
        detail = str(error)
    return (
        "user",
        "Your previous answer was rejected. Use only the approved action ids listed "
        "above, each at most once, and return the whole plan again.\n\n" + detail,
    )


def _ask(structured: Any, chat: list[Any], keys: set[str], config: Any) -> GeneratedCarePlan:
    """Call the model until the plan validates against the listed ids, or give up."""
    last: Exception | None = None
    for _ in range(MAX_ATTEMPTS):
        raw = structured.invoke(chat) if config is None else structured.invoke(chat, config=config)
        try:
            plan = GeneratedCarePlan.model_validate(raw)
            plan.check_against(keys)
        except (ValidationError, ValueError) as error:
            last = error
            chat = [*chat, _repair_message(error)]
            continue
        return plan
    raise RuntimeError(f"no valid plan after {MAX_ATTEMPTS} attempt(s): {last}")


def _invoke(
    messages: Any,
    config: AdviceConfig,
    prompt: ChatPromptClient | None,
    payload: AdviceInput,
) -> tuple[GeneratedCarePlan, str | None]:
    """Structured-output calls (with repair), traced when the project is configured."""
    # Imported here so tests (and the mock pipeline fixtures) can patch it.
    from mlops.factory import get_llm

    model = get_llm(COMPONENT, temperature=config.temperature, model_override=config.model)
    structured = model.with_structured_output(GeneratedCarePlan)
    # Tuples, not a ChatPromptTemplate: the compiled text contains JSON braces
    # that a template would read as variables.
    chat = [(m["role"], m["content"]) for m in messages]
    keys = set(payload.action_keys())

    if not is_configured(COMPONENT):
        return _ask(structured, chat, keys, None), None

    invoke_config: RunnableConfig = {
        "callbacks": [get_callback_handler(COMPONENT)],
        "run_name": "diagnose",
    }
    # One trace per plan; `propagate_attributes(prompt=…)` ties the generation to
    # the prompt version, so the Langfuse UI shows each version's real use, and
    # its metadata makes the trace findable by run.
    with (
        get_client(COMPONENT).start_as_current_observation(
            name="advice.generate_advice",
            as_type="chain",
            input={
                **payload.trace_metadata(),
                "health_status": payload.health_status,
                "fired_checks": [c.slug for c in payload.fired_checks],
                "approved_actions": len(payload.approved_actions),
            },
        ) as span,
        propagate_attributes(prompt=prompt, metadata=payload.trace_metadata()),
    ):
        output = _ask(structured, chat, keys, invoke_config)
        span.update(output={"status_label": output.status_label, "actions": len(output.actions)})
        return output, span.trace_id
