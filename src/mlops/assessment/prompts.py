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

PROBE_VARIABLES: Final[tuple[str, ...]] = ("probe_checks",)

RETIRED_VARIABLES: Final[dict[str, str]] = {
    "retrieved_passages": "",
}

#: How long a fetched prompt is reused before Langfuse is asked again. This is
#: the request path, so a promoted label takes effect within a minute.
CACHE_TTL_SECONDS: Final[int] = 60

#: The text :mod:`mlops.assessment.seed` registers. It asks for exactly
BOOTSTRAP_PROMPT: Final[str] = """\
You inspect two photographs of the same plant and describe only what is visible.
{{probe_checks}}

Image input:
- Image 1 = first plant observation
- Image 2 = second plant observation

Task:
Describe both plant images separately, then describe only the visible differences between them. Focus on leaf posture, leaf color, visible damage, pot or soil context if visible, and any other visible cues.

Important:
- Describe Image 1 and Image 2 separately before describing differences.
- Do not diagnose the biological cause.
- Do not give care advice.
- Do not infer dates, watering, disease, or causes from the image.
- Do not claim anything that is not visible.
- If a region is unclear, occluded, or outside an image, say cannot_tell for that field.

Return ONLY valid JSON in exactly this format:
{
  "image_1_observation": {
    "image_description": "one or two sentences describing image 1",
    "leaf_posture": "visible posture in image 1",
    "leaf_color": "visible color patterns in image 1",
    "visible_damage": "visible damage in image 1",
    "visible_stress_level": "none | mild | moderate | severe | cannot_tell"
  },
  "image_2_observation": {
    "image_description": "one or two sentences describing image 2",
    "leaf_posture": "visible posture in image 2",
    "leaf_color": "visible color patterns in image 2",
    "visible_damage": "visible damage in image 2",
    "visible_stress_level": "none | mild | moderate | severe | cannot_tell"
  },
  "visible_differences": [
    "short visible difference between image 1 and image 2"
  ],
  "current_observations": [
    {
      "type": "leaf_yellowing",
      "severity": "mild",
      "description": "Yellow discoloration is visible on parts of the leaves."
    },
    {
      "type": "brown_edges",
      "severity": "mild",
      "description": "Brown discoloration is visible along some leaf edges."
    }
  ],
  "comparison_summary": "short description of how the visible plant condition differs",
  "model_confidence": 0.0,

Field rules:
- Evaluate every human-approved check in probe_checks and return its result in probe_findings.
- Each answer must be exactly yes, no, or cannot_tell.
- The text "not this" describes a harmless look-alike; it is not an answer value.
- Only report visible Image 2 symptoms supported by a yes probe finding.
- A no or cannot_tell finding must not create a current observation.
- current_observations.type must be a generic type from the list below.
  Never put a probe slug or care_need in type.
- If every check is answered no, set Image 2 visible_stress_level to none
  and current_observations to [].
- Use separate observations for Image 1 and Image 2.
- visible_stress_level must be exactly one of: none, mild, moderate, severe, cannot_tell.
- Use visible_stress_level "none" when the plant appears visually healthy, with upright or naturally relaxed leaves, mostly even green color, and no clear visible damage.
- Use visible_stress_level "mild" when there are small visible concerns, such as slight drooping, slight yellowing, minor brown tips, or limited edge damage, while most leaves still look healthy.
- Use visible_stress_level "moderate" when stress is clearly visible across multiple leaves, such as noticeable drooping, yellowing, curling, browning edges, dry tips, or a generally weakened posture.
- Use visible_stress_level "severe" when the plant appears strongly stressed, with collapsed or heavily drooping leaves, widespread yellow/brown areas, extensive dry or dead-looking tissue, or severe visible damage.
- Use visible_stress_level "cannot_tell" when the plant condition cannot be judged because the plant is unclear, heavily occluded, too cropped, or the relevant regions are not visible.
- visible_differences should list concrete visual differences only.
- current_observations should describe visible health concerns in Image 2 only.
- current_observations should be an empty list when Image 2 looks healthy or when no concrete concern is visible.
- current_observations severity must be exactly one of: mild, moderate, severe.
- current_observations type must be exactly one of: leaf_yellowing, brown_edges, drooping, curling, visible_damage, discoloration, other.
- Use one current_observations item per visible symptom. Do not combine symptoms into one type such as yellowing_brown_edges.
- Each current_observations item must describe only the symptom specified by its type.
- Write a distinct, symptom-specific description for each observation.
- For leaf_yellowing, describe only the visible yellow discoloration, including its location and extent when identifiable.
- For brown_edges, describe only the visible brown discoloration along leaf edges or tips.
- For drooping, describe only the visible downward bending or hanging of leaves.
- For curling, describe only the visible curling or rolling of leaves.
- Do not mention other symptom types in the same description.
- Do not reuse the same description across different observation types.
- Describe the visible evidence rather than making general statements about plant stress.
- If the location or extent of a symptom is unclear, do not invent those details.
- The JSON examples are illustrative only. Include a symptom only when it is clearly visible in Image 2; do not copy example observations.
- Do not copy the list of allowed types into the type field.
- comparison_summary should be short and grounded in visible cues.
- model_confidence should be your own stated confidence from 0.0 to 1.0 that the visual observations are correct. This value will be recorded but not trusted as the main confidence.
"""

#: The ``config`` :mod:`mlops.assessment.seed` registers with
#: :data:`BOOTSTRAP_PROMPT`. ``model`` is not here: it is the served model id,
#: which only the deployment knows, so the seed CLI requires ``--model``.
BOOTSTRAP_CONFIG: Final[dict[str, Any]] = {
    "runs": 5,
    "temperature": 0.3,
    # The probes_2-style prompt asks for the observation JSON only.
    "max_new_tokens": 1024,
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

