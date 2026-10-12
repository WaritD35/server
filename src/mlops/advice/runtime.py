"""Execution code for ``advice`` — diagnosis and ranked care plan generation.

Owner: TODO

``src/advice`` calls :func:`generate_advice` from the pipeline's advice stage:
resolve the ``production`` prompt, call the LLM with structured output, trace it
into the advice Langfuse project.

Inputs are plain values — ``mlops`` must not import a bounded context, so the
calling context flattens its own models onto :class:`AdviceInput`.

## Degrading, not failing

Advice runs between assessment and companion, and the companion stage must
still run, so this function always returns a plan:

```
Langfuse chat prompt (production label) ──(unavailable)──▶ bootstrap prompt
      │  compiled with AdviceInput                          (prompts.BOOTSTRAP_*)
      ▼
LLM.with_structured_output(GeneratedCarePlan) ──(fails)──▶ rule-based fallback plan
```

:class:`AdviceResult` says which path produced it: ``prompt_version`` is
``None`` for the bootstrap prompt, and ``source`` is ``"fallback"`` for the
rule-based plan. An experiment pins ``version`` and gets **no** prompt
fallback — a run scored against the wrong prompt is worse than no run.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from langfuse import propagate_attributes
from pydantic import BaseModel, Field

from mlops.advice import prompts
from mlops.client import get_callback_handler, get_client, is_configured
from mlops.settings import PRODUCTION_LABEL, Component

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from langfuse.model import ChatPromptClient

logger = logging.getLogger(__name__)

COMPONENT: Final = Component.ADVICE


class GeneratedCareAction(BaseModel):
    id: str = Field(default_factory=lambda: f"act_{uuid.uuid4().hex[:8]}")
    priority: int = 1
    action: str
    label: str
    type: str = "other"  # 'water' | 'move' | 'inspect' | 'other'


class GeneratedCarePlan(BaseModel):
    status_label: str
    assessment: str
    confidence: float = 1.0
    actions: list[GeneratedCareAction] = Field(default_factory=list)


@dataclass(frozen=True)
class AdviceInput:
    """What ``src/advice`` passes in."""

    plant_id: str
    health_status: str
    symptoms: list[dict[str, Any]] = field(default_factory=list)
    observation_history: list[dict[str, Any]] = field(default_factory=list)
    knowledge_context: str | None = None
    trigger_reason: str | None = None
    confidence_agreement: float | None = None
    #: The orchestrator run, for tracing only (Langfuse trace metadata).
    run_id: str | None = None

    def trace_metadata(self) -> dict[str, str]:
        """Searchable trace metadata: ``plant_id`` and, when known, ``run_id``."""
        metadata = {"plant_id": self.plant_id}
        if self.run_id:
            metadata["run_id"] = self.run_id
        return metadata

    def as_prompt_variables(self) -> dict[str, str]:
        """The values the Langfuse template is compiled with.

        Keys are exactly :data:`~mlops.advice.prompts.DIAGNOSE_VARIABLES`.
        """
        return {
            "plant_id": self.plant_id,
            "health_status": self.health_status,
            "trigger_reason": self.trigger_reason or "Health change",
            "symptoms": json.dumps(self.symptoms),
            "observation_history": json.dumps(self.observation_history),
            "knowledge_context": self.knowledge_context or "No specific knowledge retrieved.",
        }


@dataclass(frozen=True)
class AdviceResult:
    """What comes back to the advice service layer."""

    care_plan_id: str
    status_label: str
    assessment: str
    confidence: float
    actions: list[dict[str, Any]]
    trace_id: str | None = None
    #: ``llm`` — the model answered; ``fallback`` — the rule-based plan.
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


def _generate_fallback(payload: AdviceInput) -> GeneratedCarePlan:
    """Fallback if LLM call fails or is unconfigured."""
    symptom_types = [s.get("type", "symptom") for s in payload.symptoms if isinstance(s, dict)]
    symptom_str = ", ".join(symptom_types) if symptom_types else "issue"
    return GeneratedCarePlan(
        status_label=f"{payload.health_status.replace('_', ' ').capitalize()}: {symptom_str}",
        assessment=(
            f"Observation detected {symptom_str} with {payload.health_status} status. "
            "Please inspect soil moisture and lighting."
        ),
        confidence=0.8,
        actions=[
            GeneratedCareAction(
                id=f"act_{uuid.uuid4().hex[:8]}",
                priority=1,
                action="Inspect soil moisture with your finger 2 inches deep.",
                label="Check soil",
                type="inspect",
            ),
            GeneratedCareAction(
                id=f"act_{uuid.uuid4().hex[:8]}",
                priority=2,
                action="Ensure plant is receiving adequate indirect sunlight.",
                label="Check light",
                type="move",
            ),
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
    """Produce a diagnosis and ranked actions.

    ``label`` / ``version`` / ``overrides`` exist so ``experiment.py`` can pin a
    prompt version and drive this exact function — experiments and production
    then share one code path. The request path passes none of them.
    """
    prompt = _resolve_prompt(label, version)
    variables = payload.as_prompt_variables()
    if prompt is not None:
        messages = prompt.compile(**variables)
        config = AdviceConfig.from_prompt_config(prompt.config, overrides=overrides)
    else:
        messages = prompts.compile_bootstrap(variables)
        config = AdviceConfig.from_prompt_config(prompts.BOOTSTRAP_CONFIG, overrides=overrides)

    source = "llm"
    trace_id: str | None = None
    try:
        output, trace_id = _invoke(messages, config, prompt, payload)
    except Exception:
        # Graceful fallback on LLM failure or missing API key during local test runs
        logger.warning("advice LLM call failed; using the rule-based plan", exc_info=True)
        output = _generate_fallback(payload)
        source = "fallback"

    return AdviceResult(
        care_plan_id=f"cp_{uuid.uuid4().hex[:12]}",
        status_label=output.status_label,
        assessment=output.assessment,
        confidence=output.confidence,
        actions=[action.model_dump() for action in output.actions],
        trace_id=trace_id,
        source=source,
        prompt_name=prompt.name if prompt is not None else prompts.DIAGNOSE_PROMPT,
        prompt_version=prompt.version if prompt is not None else None,
    )


def _invoke(
    messages: Any,
    config: AdviceConfig,
    prompt: ChatPromptClient | None,
    payload: AdviceInput,
) -> tuple[GeneratedCarePlan, str | None]:
    """One structured-output call, traced when the advice project is configured."""
    # Imported here so tests (and the mock pipeline fixtures) can patch it.
    from mlops.factory import get_llm

    model = get_llm(COMPONENT, temperature=config.temperature, model_override=config.model)
    structured = model.with_structured_output(GeneratedCarePlan)
    chat = [(m["role"], m["content"]) for m in messages]

    if not is_configured(COMPONENT):
        return GeneratedCarePlan.model_validate(structured.invoke(chat)), None

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
            input={**payload.trace_metadata(), "health_status": payload.health_status},
        ) as span,
        propagate_attributes(prompt=prompt, metadata=payload.trace_metadata()),
    ):
        output = GeneratedCarePlan.model_validate(structured.invoke(chat, config=invoke_config))
        span.update(output={"status_label": output.status_label, "actions": len(output.actions)})
        return output, span.trace_id
