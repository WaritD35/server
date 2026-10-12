"""Prompts for the ``advice`` Langfuse project.

Owner: TODO

Keep the prompt names for this project here so ``runtime`` and ``experiment``
resolve them through one place. The live prompt bodies and their
hyper-parameters live in Langfuse (a version's ``config``).

:data:`BOOTSTRAP_MESSAGES` / :data:`BOOTSTRAP_CONFIG` are what
:mod:`mlops.advice.seed` registers as a new version — a starting point, not the
source of truth; after the first version, edit and promote in the Langfuse UI.
Unlike ``assessment``, the runtime also falls back to them when the
``production`` prompt cannot be fetched, because advice degrades rather than
failing the pipeline run (see :func:`mlops.advice.runtime.generate_advice`).
"""

from __future__ import annotations

import re
from typing import Any, Final

from langfuse.model import ChatPromptClient

from mlops.client import get_client
from mlops.settings import PRODUCTION_LABEL, Component

COMPONENT: Final = Component.ADVICE

#: Verdicts + history + approved checks and actions → diagnosis and a ranked
#: plan. A **chat** prompt whose variables are exactly :data:`DIAGNOSE_VARIABLES`;
#: its ``config`` supplies the model hyper-parameters (see
#: :class:`mlops.advice.runtime.AdviceConfig`).
DIAGNOSE_PROMPT: Final[str] = "advice/diagnose"

#: The variables the ``DIAGNOSE_PROMPT`` template is compiled with — the
#: contract between the prompt author and :mod:`mlops.advice.runtime`.
#: ``fired_checks`` lists the approved checks that fired; ``approved_actions``
#: lists the approved actions the plan may use, one per line as ``A1: …``.
#: There is no free-text knowledge variable: only approved knowledge is sent.
DIAGNOSE_VARIABLES: Final[tuple[str, ...]] = (
    "plant_id",
    "health_status",
    "trigger_reason",
    "symptoms",
    "observation_history",
    "fired_checks",
    "approved_actions",
)

#: How long a fetched prompt is reused before Langfuse is asked again. Advice
#: runs in the pipeline, so a promoted label takes effect within a minute.
CACHE_TTL_SECONDS: Final[int] = 60

#: The messages :mod:`mlops.advice.seed` registers, in Langfuse's ``{{var}}``
#: syntax. The output shape is enforced by structured output
#: (:class:`mlops.advice.runtime.GeneratedCarePlan`), not by this text.
BOOTSTRAP_MESSAGES: Final[list[dict[str, str]]] = [
    {
        "role": "system",
        "content": """\
You are an expert Plant Care Advisor Agent.
Your job is to explain what is wrong with the plant and to build a care plan
from human-approved care actions only.

PROCESS GUIDELINES:
1. Examine the symptoms, history, and the approved checks that fired.
2. Formulate a short headline status_label (e.g. "Overwatering stress", "Dry tip warning").
3. Formulate an assessment explaining the likely cause, based only on what was observed
   and on the checks that fired.
4. Choose the care actions from the approved actions list. Never write a new action.
   - approved_action_id: the id from the list (e.g. "A1"); each id at most once.
   - priority: 1 = do first. Prefer actions of the checks that fired and higher urgency.
   - label: concise 2-3 word button label (e.g. "Pause water", "Move plant", "Drain tray").
   - type: categorical type: "water" | "move" | "inspect" | "other".
   Leave out actions that do not fit what was observed, but keep at least one.
""",
    },
    {
        "role": "user",
        "content": """\
Plant ID: {{plant_id}}
Health Status: {{health_status}}
Trigger Reason: {{trigger_reason}}
Symptoms: {{symptoms}}
Observation History (Past Days): {{observation_history}}

Approved checks that fired:
{{fired_checks}}

Approved actions (choose only from these, by id):
{{approved_actions}}

Please provide the diagnosis and prioritized care plan.""",
    },
]

#: The ``config`` :mod:`mlops.advice.seed` registers. ``model`` is added only
#: when the seed CLI is given ``--model``; see
#: :meth:`mlops.advice.runtime.AdviceConfig.from_prompt_config` for why advice
#: may run without one.
BOOTSTRAP_CONFIG: Final[dict[str, Any]] = {"temperature": 0.2}

_VARIABLE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def compile_bootstrap(variables: dict[str, str]) -> list[dict[str, str]]:
    """:data:`BOOTSTRAP_MESSAGES` compiled the way Langfuse compiles a chat prompt."""
    return [
        {
            "role": message["role"],
            "content": _VARIABLE.sub(
                lambda m: variables.get(m.group(1), m.group(0)), message["content"]
            ),
        }
        for message in BOOTSTRAP_MESSAGES
    ]


def get(
    name: str = DIAGNOSE_PROMPT,
    *,
    label: str | None = PRODUCTION_LABEL,
    version: int | None = None,
) -> ChatPromptClient:
    """Fetch a chat prompt version from this component's Langfuse project.

    Pass ``label`` in the request path (always :data:`PRODUCTION_LABEL`) or
    ``version`` from an experiment, so a dataset run is reproducible. Passing
    both is a mistake — a pinned version and a moving label disagree the moment
    the label is promoted.
    """
    if version is not None and label is not None and label != PRODUCTION_LABEL:
        raise ValueError("pass either `label` or `version`, not both")
    kwargs: dict[str, Any] = {"type": "chat", "cache_ttl_seconds": CACHE_TTL_SECONDS}
    if version is not None:
        kwargs["version"] = version
    else:
        kwargs["label"] = label
    prompt = get_client(COMPONENT).get_prompt(name, **kwargs)
    # `type="chat"` is what we ask for, not what we are guaranteed: a text
    # prompt authored under this name would compile to a string, not messages.
    if not isinstance(prompt, ChatPromptClient):
        raise TypeError(f"Langfuse prompt {name!r} is a text prompt; it must be a chat prompt")
    return prompt
