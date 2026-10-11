"""Execution code for ``assessment`` — the VLM probe call.

Owner: TODO

This is what ``src/assessment`` calls at request time: resolve the production
prompt, call the VLM on the previous/current frame pair, parse the answer, trace
it into the assessment Langfuse project.

Inputs are plain values — ``mlops`` must not import a bounded context, so the
calling context flattens its own models onto :class:`ProbeInput`.

## The loop

```
Langfuse text prompt (production label) ── config: model, runs, temperature,
      │  compiled with plant_knowledge                       max_new_tokens
      ▼
backend.generate(text, [previous, current]) × runs
      │
      ▼
extract_json_object ─▶ RunObservation ──(invalid)──▶ one repair ──▶ drop run
      │
      ▼
fewer than ceil(runs / 2) valid ─▶ AssessmentRunError
      │
      ▼
aggregate_observations ─▶ ProbeVerdict (+ trace_id)
```

The VLM is sampled several times because one answer from a small model is
noisy; the majority over the runs that validate is the verdict. **A verdict
built from a minority of valid runs is never returned** — there is no "mostly
valid" path out of this module.

Every hyper-parameter, including which model, comes from the prompt version's
``config``; there are no defaults here. Anything the call depends on being
absent — Langfuse credentials, the prompt, a config key, the VLM itself —
raises :class:`AssessmentUnavailableError`, which callers map to 503.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Final

from langfuse import propagate_attributes
from pydantic import ValidationError

from mlops.assessment import prompts
from mlops.assessment.aggregate import (
    AggregateError,
    ProbeVerdict,
    aggregate_observations,
    extract_json_object,
)
from mlops.assessment.backends import (
    VLMBackend,
    VLMBackendUnavailableError,
    get_vlm_backend,
)
from mlops.assessment.schemas import RunObservation
from mlops.client import get_client
from mlops.settings import PRODUCTION_LABEL, Component

if TYPE_CHECKING:
    from langfuse import Langfuse

__all__ = [
    "AssessmentRunError",
    "AssessmentUnavailableError",
    "ProbeConfig",
    "ProbeInput",
    "ProbeVerdict",
    "run_probe",
]

COMPONENT: Final = Component.ASSESSMENT

#: Every key the prompt version's ``config`` must carry.
CONFIG_KEYS: Final[tuple[str, ...]] = ("model", "runs", "temperature", "max_new_tokens")


class AssessmentUnavailableError(RuntimeError):
    """The assessment cannot run at all — nothing about the frames is known.

    Langfuse credentials missing, the prompt fetch failed, its config lacks a
    key, or the VLM backend is misconfigured or unreachable.
    """


class AssessmentRunError(RuntimeError):
    """Fewer than half the runs (rounded up) produced a valid observation.

    Carries the last failure so a human reading the job log can tell a prompt
    problem (the same rule fails every time) from a flaky model.
    """

    def __init__(self, runs: int, valid: int, last_error: Exception | None) -> None:
        self.runs = runs
        self.valid = valid
        self.last_error = last_error
        super().__init__(
            f"only {valid} of {runs} VLM run(s) produced a valid observation "
            f"(need {math.ceil(runs / 2)}); last error: {last_error}"
        )


@dataclass(frozen=True)
class ProbeInput:
    """What ``src/assessment`` passes in."""

    #: For tracing only.
    plant_id: str
    #: Scientific name, for tracing.
    species: str
    #: Care knowledge for the species; ``""`` when there is none.
    plant_knowledge: str
    #: JPEG bytes. The prompt calls this frame Image 1.
    previous_image: bytes
    #: JPEG bytes. The prompt calls this frame Image 2.
    current_image: bytes

    def as_prompt_variables(self) -> dict[str, str]:
        """The values the Langfuse template is compiled with.

        Keys are exactly :data:`~mlops.assessment.prompts.PROBE_VARIABLES`.
        """
        knowledge = self.plant_knowledge.strip()
        return {"plant_knowledge": f"Plant knowledge:\n{knowledge}" if knowledge else ""}


@dataclass(frozen=True)
class ProbeConfig:
    """Hyper-parameters, read from the Langfuse prompt version's ``config``.

    All four are required: the model a verdict came from, and how it was
    sampled, must be recoverable from the prompt version it names.
    """

    model: str
    runs: int
    temperature: float
    max_new_tokens: int

    @property
    def min_valid_runs(self) -> int:
        return math.ceil(self.runs / 2)

    @classmethod
    def from_prompt_config(
        cls, config: Any, *, overrides: dict[str, Any] | None = None
    ) -> ProbeConfig:
        unknown = set(overrides or {}) - set(CONFIG_KEYS)
        if unknown:
            raise ValueError(f"unknown override(s) {sorted(unknown)}; allowed: {CONFIG_KEYS}")
        raw: dict[str, Any] = dict(config) if isinstance(config, dict) else {}
        raw.update(overrides or {})
        missing = [key for key in CONFIG_KEYS if raw.get(key) in (None, "")]
        if missing:
            raise AssessmentUnavailableError(
                f"the Langfuse prompt config is missing {', '.join(missing)}: add it "
                "to the version's config (an experiment can pass it in `overrides`)"
            )
        try:
            parsed = cls(
                model=str(raw["model"]),
                runs=int(raw["runs"]),
                temperature=float(raw["temperature"]),
                max_new_tokens=int(raw["max_new_tokens"]),
            )
        except (TypeError, ValueError) as error:
            raise AssessmentUnavailableError(
                f"the Langfuse prompt config is not usable: {error}"
            ) from error
        if parsed.runs < 1 or parsed.max_new_tokens < 1 or parsed.temperature < 0:
            raise AssessmentUnavailableError(
                "the Langfuse prompt config needs runs >= 1, max_new_tokens >= 1 "
                f"and temperature >= 0, got {parsed}"
            )
        return parsed


def run_probe(
    probe: ProbeInput,
    *,
    label: str | None = PRODUCTION_LABEL,
    version: int | None = None,
    overrides: dict[str, Any] | None = None,
) -> ProbeVerdict:
    """Assess one previous/current frame pair and return the aggregated verdict.

    ``label`` / ``version`` / ``overrides`` exist so ``experiment.py`` can pin a
    prompt version and drive this exact function — experiments and production
    then share one code path. The request path passes none of them.
    ``overrides`` may replace any of :data:`CONFIG_KEYS`.

    Raises:
        AssessmentUnavailableError: Langfuse, the prompt, its config or the VLM
            backend is missing or unreachable.
        AssessmentRunError: fewer than ``ceil(runs / 2)`` runs were valid.
    """
    try:
        prompt = prompts.get(prompts.PROBE_PROMPT, label=label, version=version)
    except Exception as error:
        raise AssessmentUnavailableError(
            f"cannot fetch Langfuse prompt {prompts.PROBE_PROMPT!r}: {error}"
        ) from error
    config = ProbeConfig.from_prompt_config(prompt.config, overrides=overrides)
    try:
        backend = get_vlm_backend()
    except VLMBackendUnavailableError as error:
        raise AssessmentUnavailableError(str(error)) from error

    text = prompt.compile(**probe.as_prompt_variables())
    images = [probe.previous_image, probe.current_image]

    client = get_client(COMPONENT)
    # One trace per frame pair, with each run (and its repair) nested inside it
    # as a generation; `propagate_attributes(prompt=…)` ties those generations
    # to the prompt version.
    with (
        client.start_as_current_observation(
            name="assessment.run_probe",
            as_type="chain",
            input={"plant_id": probe.plant_id, "species": probe.species},
        ) as span,
        propagate_attributes(prompt=prompt),
    ):
        valid: list[RunObservation] = []
        last_error: Exception | None = None
        for index in range(1, config.runs + 1):
            try:
                valid.append(_run_once(client, backend, text, images, config, index=index))
            except (AggregateError, ValidationError) as error:
                last_error = error

        if len(valid) < config.min_valid_runs:
            span.update(
                output={"valid_runs": len(valid), "runs": config.runs},
                level="ERROR",
                status_message=str(last_error),
            )
            raise AssessmentRunError(config.runs, len(valid), last_error)

        verdict = aggregate_observations(valid)
        span.update(
            output={
                "health_status": verdict.health_status,
                "observations": verdict.observations,
                "consensus": verdict.consensus,
            }
        )
        trace_id: str | None = span.trace_id

    return replace(verdict, trace_id=trace_id)


def _run_once(
    client: Langfuse,
    backend: VLMBackend,
    text: str,
    images: list[bytes],
    config: ProbeConfig,
    *,
    index: int,
) -> RunObservation:
    """One run: generate, validate, and on failure one repair with the errors fed back."""
    raw = _generate(client, backend, text, images, config, name=f"vlm-run-{index}")
    try:
        return _parse(raw)
    except (AggregateError, ValidationError) as error:
        repair = _repair_prompt(text, raw, error)
    raw = _generate(client, backend, repair, images, config, name=f"vlm-run-{index}-repair")
    return _parse(raw)


def _generate(
    client: Langfuse,
    backend: VLMBackend,
    text: str,
    images: list[bytes],
    config: ProbeConfig,
    *,
    name: str,
) -> str:
    with client.start_as_current_observation(
        name=name,
        as_type="generation",
        model=config.model,
        model_parameters={
            "temperature": config.temperature,
            "max_new_tokens": config.max_new_tokens,
        },
        input=text,
    ) as generation:
        try:
            raw = backend.generate(
                text,
                images,
                model=config.model,
                temperature=config.temperature,
                max_new_tokens=config.max_new_tokens,
            )
        except VLMBackendUnavailableError as error:
            raise AssessmentUnavailableError(str(error)) from error
        generation.update(output=raw)
    return raw


def _parse(raw: str) -> RunObservation:
    return RunObservation.model_validate(extract_json_object(raw))


def _repair_prompt(text: str, raw: str, error: Exception) -> str:
    """The original task, the rejected answer and exactly what was wrong with it.

    The backends are single-turn, so the repair is the original prompt extended
    rather than a follow-up message.
    """
    if isinstance(error, ValidationError):
        detail = json.dumps(
            [
                {"field": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]}
                for e in error.errors()
            ],
            ensure_ascii=False,
            indent=2,
        )
    else:
        detail = str(error)
    return (
        f"{text}\n\n"
        "Your previous answer was rejected by validation. Fix exactly these "
        "problems and return the whole JSON object again — do not apologise, do "
        "not explain, return only the corrected JSON.\n\n"
        f"Problems:\n{detail}\n\n"
        f"Your previous answer:\n{raw}"
    )
