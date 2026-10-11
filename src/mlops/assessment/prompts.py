"""Prompts for the ``assessment`` Langfuse project.

Owner: TODO

Keep the prompt names for this project here so ``runtime`` and ``experiment``
resolve them through one place. The prompt bodies and their hyper-parameters
live in Langfuse (a version's ``config``), never in this repository.
"""

from __future__ import annotations

from typing import Any, Final

from langfuse.model import TextPromptClient

from mlops.client import get_client
from mlops.settings import PRODUCTION_LABEL, Component

COMPONENT: Final = Component.ASSESSMENT

#: Previous/current frame pair → one JSON observation. A **text** prompt: the
#: runtime sends the compiled text as the text part of a single user message,
#: next to the two images. Its ``config`` must carry ``model``, ``runs``,
#: ``temperature`` and ``max_new_tokens`` (see
#: :class:`mlops.assessment.runtime.ProbeConfig`).
PROBE_PROMPT: Final[str] = "assessment/plant_observation_pair"

PROBE_PROMPT_TYPE: Final[str] = "text"

#: The variables the ``PROBE_PROMPT`` template is compiled with — the contract
#: between the prompt author and :mod:`mlops.assessment.runtime`.
#: ``plant_knowledge`` is the whole optional section (``"Plant knowledge:\n…"``),
#: or ``""`` when the species has none, so the template needs no conditional.
PROBE_VARIABLES: Final[tuple[str, ...]] = ("plant_knowledge",)

#: How long a fetched prompt is reused before Langfuse is asked again. This is
#: the request path, so a promoted label takes effect within a minute.
CACHE_TTL_SECONDS: Final[int] = 60


def get(
    name: str = PROBE_PROMPT,
    *,
    label: str | None = PRODUCTION_LABEL,
    version: int | None = None,
) -> TextPromptClient:
    """Fetch a text prompt version from this component's Langfuse project.

    Pass ``label`` in the request path (always :data:`PRODUCTION_LABEL`) or
    ``version`` from an experiment, so a dataset run is reproducible. Passing
    both is a mistake — a pinned version and a moving label disagree the moment
    the label is promoted.
    """
    if version is not None and label is not None and label != PRODUCTION_LABEL:
        raise ValueError("pass either `label` or `version`, not both")
    kwargs: dict[str, Any] = {
        "type": PROBE_PROMPT_TYPE,
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
    }
    if version is not None:
        kwargs["version"] = version
    else:
        kwargs["label"] = label
    prompt = get_client(COMPONENT).get_prompt(name, **kwargs)
    # `type="text"` is what we ask for, not what we are guaranteed: a chat
    # prompt authored under this name would compile to a message list, not the
    # single text part the VLM request needs.
    if not isinstance(prompt, TextPromptClient):
        raise TypeError(
            f"Langfuse prompt {name!r} is a chat prompt; it must be a text prompt"
        )
    return prompt
