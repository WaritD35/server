"""Execution code for ``advice`` — diagnosis and ranked care plan generation."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from mlops.client import get_callback_handler, is_configured
from mlops.settings import PRODUCTION_LABEL, Component

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class AdviceResult:
    """What comes back to the advice service layer."""

    care_plan_id: str
    status_label: str
    assessment: str
    confidence: float
    actions: list[dict[str, Any]]
    trace_id: str | None = None


DEFAULT_SYSTEM_PROMPT = """You are an expert Plant Care Advisor Agent.
Your job is to diagnose plant health issues based on visual observations
and recommend prioritized care actions.

PROCESS GUIDELINES:
1. Examine the symptoms, history, and botanical knowledge provided.
2. Formulate a short headline status_label (e.g. "Overwatering stress", "Dry tip warning").
3. Formulate a clinical assessment explaining the likely root cause.
4. Provide prioritized practical care actions (priority 1 = most urgent).
   - action: full detailed botanical instruction string.
   - label: concise 2-3 word button label (e.g. "Pause water", "Move plant", "Drain tray").
   - type: categorical type: "water" | "move" | "inspect" | "other".
"""


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


def generate_advice(
    payload: AdviceInput,
    *,
    label: str | None = PRODUCTION_LABEL,
    version: int | None = None,
    overrides: dict[str, Any] | None = None,
) -> AdviceResult:
    """Produce a diagnosis and ranked actions."""
    from langchain_core.prompts import ChatPromptTemplate

    care_plan_id = f"cp_{uuid.uuid4().hex[:12]}"
    from langchain_core.callbacks import BaseCallbackHandler
    callbacks: list[BaseCallbackHandler] = []
    if is_configured(Component.ADVICE):
        try:
            callbacks.append(get_callback_handler(Component.ADVICE))
        except Exception:
            logger.warning("Failed to initialise Langfuse callback for advice", exc_info=True)

    # Build prompt inputs
    symptoms_text = json.dumps(payload.symptoms)
    history_text = json.dumps(payload.observation_history)
    knowledge_text = payload.knowledge_context or "No specific knowledge retrieved."

    user_content = (
        f"Plant ID: {payload.plant_id}\n"
        f"Health Status: {payload.health_status}\n"
        f"Trigger Reason: {payload.trigger_reason or 'Health change'}\n"
        f"Symptoms: {symptoms_text}\n"
        f"Observation History (Past Days): {history_text}\n"
        f"Botanical Knowledge Context: {knowledge_text}\n\n"
        "Please provide the diagnosis and prioritized care plan."
    )

    try:
        from mlops.factory import get_llm
        model = get_llm(Component.ADVICE, temperature=0.2)
        structured_llm = model.with_structured_output(GeneratedCarePlan)
        prompt = ChatPromptTemplate.from_messages([
            ("system", DEFAULT_SYSTEM_PROMPT),
            ("user", user_content),
        ])
        chain = prompt | structured_llm
        from langchain_core.runnables import RunnableConfig
        cfg: RunnableConfig = {"callbacks": callbacks} if callbacks else {}
        raw = chain.invoke({}, config=cfg)
        output = GeneratedCarePlan.model_validate(raw)
    except Exception:
        # Graceful fallback on LLM failure or missing API key during local test runs
        output = _generate_fallback(payload)

    return AdviceResult(
        care_plan_id=care_plan_id,
        status_label=output.status_label,
        assessment=output.assessment,
        confidence=output.confidence,
        actions=[action.model_dump() for action in output.actions],
    )
