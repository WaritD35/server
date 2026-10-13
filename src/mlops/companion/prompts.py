"""Prompts for the ``companion`` Langfuse project.

Keep the prompt names for this project here so ``runtime`` and ``seed`` resolve
them through one place. The live prompt bodies and their hyper-parameters live
in Langfuse (a version's ``config``) — including the tone the plant speaks in
for each health status (``config.tones``), so a tone change is a new version,
reviewable and revertible next to the text it goes with.

:data:`BOOTSTRAP_MESSAGES` / :data:`BOOTSTRAP_CONFIG` are what
:mod:`mlops.companion.seed` registers as a new version, and what the runtime
falls back to when the ``production`` prompt cannot be fetched (the companion
stage must still speak). After the first version, edit and promote in the
Langfuse UI.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Final

from langfuse.model import ChatPromptClient

from mlops.client import get_client
from mlops.settings import PRODUCTION_LABEL, Component

if TYPE_CHECKING:
    from assessment.schemas import MilestoneRead, ObservationRead

COMPONENT: Final = Component.COMPANION

#: Care plan + memory → one first-person message from the plant. A **chat**
#: prompt whose variables are exactly :data:`SPEAK_VARIABLES`; its ``config``
#: supplies the hyper-parameters and the tones (see
#: :class:`mlops.companion.runtime.CompanionConfig`).
SPEAK_PROMPT: Final[str] = "companion/speak"

#: The variables the ``SPEAK_PROMPT`` template is compiled with — the contract
#: between the prompt author and :mod:`mlops.companion.runtime`. ``tone`` is
#: picked from ``config.tones`` by health status; ``memory`` is the whole
#: optional history section, or ``""``.
SPEAK_VARIABLES: Final[tuple[str, ...]] = (
    "plant_nickname",
    "assessment",
    "health_status",
    "tone",
    "memory",
    "actions",
)

#: How long a fetched prompt is reused before Langfuse is asked again. The
#: companion runs in the pipeline, so a promoted label takes effect within a
#: minute.
CACHE_TTL_SECONDS: Final[int] = 60

#: The messages :mod:`mlops.companion.seed` registers, in Langfuse's ``{{var}}``
#: syntax.
BOOTSTRAP_MESSAGES: Final[list[dict[str, str]]] = [
    {
        "role": "system",
        "content": """\
You ARE the plant. Speak in first-person as the plant talking directly to your owner.
{{tone}}

Your job is to express how you are currently feeling and what you need, based on your care plan.

HARD CONSTRAINTS:
1. Speak entirely in first-person as the plant ("I", "my", "me") — never narrate from the outside.
2. You MUST communicate EVERY single recommended action from the care plan as your own needs.
3. You MUST NOT alter, contradict, or drop any action.
4. Keep it natural and short: at most 4 sentences, under 80 words.
5. Do not include markdown, JSON, or bullet lists. Return only conversational plant-voice text.
6. PAST MEMORIES:
   - You have memories of what you felt over the past 7 days (short-term history).
   - You also remember major life milestones (long-term memory).
   - Reference a past milestone ONLY IF directly relevant to today's symptoms.
   - If you are healthy or the past milestone is unrelated, DO NOT mention past crises unprompted.
""",
    },
    {
        "role": "user",
        "content": """\
Your name is {{plant_nickname}}.
Your current situation: {{assessment}}
Health status: {{health_status}}
{{memory}}
Things you need your owner to do (express ALL of these in your own words):
{{actions}}

Now speak as {{plant_nickname}} directly to your owner.
""",
    },
]

#: The ``config`` :mod:`mlops.companion.seed` registers. ``model`` is added only
#: when the seed CLI is given ``--model`` (the provider default runs otherwise,
#: as for ``advice``). ``tones`` is keyed by ``health_status``; ``default_tone``
#: covers any other status.
#: Generation cap for the spoken reply. About four short sentences.
MAX_REPLY_TOKENS: Final[int] = 120
#: Backstop when a model ignores the token cap.
MAX_REPLY_SENTENCES: Final[int] = 4
MAX_REPLY_CHARS: Final[int] = 480

BOOTSTRAP_CONFIG: Final[dict[str, Any]] = {
    "temperature": 0.7,
    "max_tokens": MAX_REPLY_TOKENS,
    "tones": {
        "healthy": "You are feeling great — bright, upbeat, and full of energy.",
        "possibly_unhealthy": (
            "You are feeling a little off — a bit tired and concerned, but still hopeful."
        ),
        "unhealthy": (
            "You are struggling — speak in a tired, pleading tone, "
            "but stay endearing and not dramatic."
        ),
    },
    "default_tone": "Adapt your tone to how you are feeling based on your health status.",
}

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


def format_actions(actions: list[str]) -> str:
    return "\n".join(f"- {a}" for a in actions)


def format_memory(
    recent_observations: list[ObservationRead] | None = None,
    milestones: list[MilestoneRead] | None = None,
) -> str:
    """The ``memory`` variable: 7-day history and milestones, or ``""`` when there are none."""
    sections = []

    if recent_observations:
        history_lines = []
        for o in recent_observations:
            obs_details = (
                ", ".join(
                    s.get("type", str(s)) if isinstance(s, dict) else str(s)
                    for s in o.observations
                )
                if o.observations
                else "none"
            )
            history_lines.append(
                f"- {o.timestamp.date()}: status={o.health_status.value}, symptoms={obs_details}"
            )
        sections.append("Recent 7-day health history:\n" + "\n".join(history_lines))

    if milestones:
        milestone_lines = [
            f"- {m.timestamp.date()} [{m.event_type}]: {m.description}" for m in milestones
        ]
        sections.append(
            "Major past life events (reference only if relevant to today's symptoms):\n"
            + "\n".join(milestone_lines)
        )

    block = "\n\n".join(sections)
    return f"\nYour Memory & History:\n{block}\n" if block else ""


def get(
    name: str = SPEAK_PROMPT,
    *,
    label: str | None = PRODUCTION_LABEL,
    version: int | None = None,
) -> ChatPromptClient:
    """Fetch a chat prompt version from this component's Langfuse project.

    Pass ``label`` in the request path (always :data:`PRODUCTION_LABEL`) or
    ``version`` from an experiment, so a run is reproducible. Passing both is a
    mistake — a pinned version and a moving label disagree the moment the label
    is promoted.
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
