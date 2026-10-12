"""Prompts for the ``assessment`` Langfuse project.

Owner: TODO

Keep the prompt names for this project here so ``runtime`` and ``experiment``
resolve them through one place. The live prompt bodies and their
hyper-parameters live in Langfuse (a version's ``config``).

:data:`BOOTSTRAP_PROMPT` / :data:`BOOTSTRAP_CONFIG` are only what
:mod:`mlops.assessment.seed` registers as a new version — a starting point, not
the source of truth. After the first version, edit and promote in the Langfuse
UI. The runtime never reads them, so a missing Langfuse prompt still raises.
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
#: ``probe_checks`` is the species' approved ``knowledge`` probes, one block
#: each, or ``""`` — the only knowledge the VLM sees. A version without
#: ``{{probe_checks}}`` still runs; its checks are not asked.
PROBE_VARIABLES: Final[tuple[str, ...]] = ("probe_checks",)

#: Variables older prompt versions still contain, compiled to ``""`` so their
#: placeholder never reaches the VLM. ``plant_knowledge`` carried free research
#: text, which assessment no longer sends.
RETIRED_VARIABLES: Final[dict[str, str]] = {"plant_knowledge": ""}

#: How long a fetched prompt is reused before Langfuse is asked again. This is
#: the request path, so a promoted label takes effect within a minute.
CACHE_TTL_SECONDS: Final[int] = 60

#: The text :mod:`mlops.assessment.seed` registers. It asks for exactly
#: :class:`mlops.assessment.schemas.RunObservation`; Image 2's
#: ``visible_stress_level`` is what
#: :func:`~mlops.assessment.aggregate.derive_run_decision` turns into the health
#: status.
BOOTSTRAP_PROMPT: Final[str] = """\
You are a plant health inspector. You are given two photos of the same potted plant,
taken from the same camera position at different times.

- Image 1 is the previous photo.
- Image 2 is the current photo.

{{probe_checks}}

Describe only what is visible. Do not guess at causes, and do not give care advice.
If something cannot be seen clearly, say "cannot_tell" instead of guessing.

Return exactly one JSON object and nothing else, with this shape:

{
  "image_1_observation": {
    "image_description": "<one sentence about the whole plant in Image 1>",
    "leaf_posture": "<upright | slightly drooping | drooping | ...>",
    "leaf_color": "<the leaf colour, including any yellow or brown areas>",
    "visible_damage": "<tears, spots, dry tips, or none>",
    "visible_stress_level": "none | mild | moderate | severe | cannot_tell"
  },
  "image_2_observation": { <the same five fields, for Image 2> },
  "visible_differences": ["<one change from Image 1 to Image 2 per item>"],
  "current_observations": [
    {
      "type": "<one of the observation types listed below>",
      "severity": "mild | moderate | severe",
      "description": "<what is visible in Image 2>"
    }
  ],
  "comparison_summary": "<one or two sentences comparing the two photos>",
  "model_confidence": <a number from 0.0 to 1.0>,
  "probe_findings": [
    {
      "slug": "<the id of a species-specific check>",
      "answer": "yes | no | cannot_tell",
      "evidence": "<what in Image 2 supports the answer>"
    }
  ]
}

Rules:
- "current_observations" lists health concerns visible in Image 2 only. Use an empty
  list when Image 2 shows none, and then set Image 2's "visible_stress_level" to "none".
- "visible_differences" is an empty list when nothing changed.
- Observation types: leaf_yellowing, brown_edges, drooping, curling, visible_damage,
  discoloration, other.
- Use only the values listed for "visible_stress_level", "type" and "severity".
- "probe_findings" has exactly one entry per species-specific check listed above, and
  an empty list when none are listed. Answer "yes" only when Image 2 looks like the
  check's "yes looks like" and not like its "not this"; answer "cannot_tell" when
  Image 2 does not show it clearly.
"""

#: The ``config`` :mod:`mlops.assessment.seed` registers with
#: :data:`BOOTSTRAP_PROMPT`. ``model`` is not here: it is the served model id,
#: which only the deployment knows, so the seed CLI requires ``--model``.
BOOTSTRAP_CONFIG: Final[dict[str, Any]] = {
    "runs": 5,
    "temperature": 0.7,
    # Room for one probe_findings entry per approved check on top of the
    # observation itself.
    "max_new_tokens": 1536,
}


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
