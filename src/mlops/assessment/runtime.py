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
      │  compiled with the approved checks                   max_new_tokens
      ▼
backend.generate(text, [previous, current]) × runs
      │
      ▼
extract_json_object ─▶ JSON Schema ─▶ RunObservation ──(invalid)──▶ one repair ──▶ drop run
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
import logging
import math
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Final

from jsonschema.exceptions import ValidationError as JsonSchemaError
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
from mlops.assessment.schemas import (
    RunObservation,
    prepare_observation_json,
    validate_observation_json,
)
from mlops.client import get_client
from mlops.settings import PRODUCTION_LABEL, Component

if TYPE_CHECKING:
    from langfuse import Langfuse

__all__ = [
    "AssessmentRunError",
    "AssessmentUnavailableError",
    "ProbeCheck",
    "ProbeConfig",
    "ProbeInput",
    "ProbeVerdict",
    "run_probe",
]

logger = logging.getLogger(__name__)

COMPONENT: Final = Component.ASSESSMENT

#: Every key the prompt version's ``config`` must carry.
CONFIG_KEYS: Final[tuple[str, ...]] = ("model", "runs", "temperature", "max_new_tokens")
MAX_PROBE_RUNS = 8
MAX_PROBE_NEW_TOKENS = 2048


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
class ProbeCheck:
    """One species-specific check: an approved ``knowledge`` probe, flattened.

    The VLM answers ``yes`` when Image 2 looks like ``worse_looks_like``.
    """

    slug: str
    question: str
    worse_looks_like: str
    better_looks_like: str
    not_this: str

    def as_prompt_lines(self) -> list[str]:
        return [
            f"- {self.slug}: {self.question}",
            f"  yes looks like: {self.worse_looks_like}",
            f"  no looks like: {self.better_looks_like}",
            f"  not this: {self.not_this}",
        ]


@dataclass(frozen=True)
class ProbeInput:
    """What ``src/assessment`` passes in."""

    #: For tracing only.
    plant_id: str
    #: Scientific name, for tracing.
    species: str
    #: JPEG bytes. The prompt calls this frame Image 1.
    previous_image: bytes
    #: JPEG bytes. The prompt calls this frame Image 2.
    current_image: bytes
    #: Free species knowledge used by the probes_2-style prompt.
    plant_knowledge: str = ""
    #: The orchestrator (or debug) run, for tracing only: it goes on the
    #: Langfuse trace's metadata so a failed ``pipeline_run`` can be found there.
    run_id: str | None = None
    #: The species' approved checks, most important first; empty when it has none.
    checks: tuple[ProbeCheck, ...] = ()

    def as_prompt_variables(self) -> dict[str, str]:
        """The values the Langfuse template is compiled with.

        Keys are exactly :data:`~mlops.assessment.prompts.PROBE_VARIABLES`.
        ``plant_knowledge`` is formatted as the optional section used by the
        older probes_2 prompt. The checks section remains available for prompt
        versions that still ask species-specific approved probes.
        """
        knowledge = self.plant_knowledge.strip()
        plant_knowledge = f"Plant knowledge:\n{knowledge}" if knowledge else ""
        checks = ""
        if self.checks:
            lines = [
                "Human-approved, species-specific assessment checks. Evaluate each "
                "check against Image 2 and return one probe_findings entry per check. "
                'Use the exact id before the colon as "slug" and answer only yes, '
                "no, or cannot_tell. The 'not this' text is a look-alike warning, "
                "not an answer value. In current_observations, report only visible "
                "symptoms supported by a yes finding; never put a probe slug or "
                "care_need in current_observations.type. Its type must be exactly "
                "one of: leaf_yellowing, brown_edges, drooping, curling, "
                "visible_damage, discoloration, other. No other type is allowed. "
                "A no or "
                "cannot_tell finding must not create an observation."
            ]
            for check in self.checks:
                lines.extend(check.as_prompt_lines())
            checks = "\n".join(lines)
        return {"plant_knowledge": plant_knowledge, "probe_checks": checks}

    def trace_metadata(self) -> dict[str, str]:
        """Searchable trace metadata: ``plant_id`` and, when known, ``run_id``."""
        metadata = {"plant_id": self.plant_id}
        if self.run_id:
            metadata["run_id"] = self.run_id
        return metadata


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
        if (
            not 1 <= parsed.runs <= MAX_PROBE_RUNS
            or not 1 <= parsed.max_new_tokens <= MAX_PROBE_NEW_TOKENS
            or parsed.temperature < 0
        ):
            raise AssessmentUnavailableError(
                "the Langfuse prompt config needs "
                f"runs 1..{MAX_PROBE_RUNS}, max_new_tokens 1..{MAX_PROBE_NEW_TOKENS} "
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

    text = _compile_prompt(prompt, {**probe.as_prompt_variables(), **prompts.RETIRED_VARIABLES})
    images = [probe.previous_image, probe.current_image]
    slugs = _asked_slugs(prompt, probe)
    client = get_client(COMPONENT)
    # One trace per frame pair, with each run (and its repair) nested inside it
    # as a generation; `propagate_attributes(prompt=…)` ties those generations
    # to the prompt version, and its metadata makes the trace findable by run.
    with (
        client.start_as_current_observation(
            name="assessment.run_probe",
            as_type="chain",
            input={**probe.trace_metadata(), "species": probe.species},
        ) as span,
        propagate_attributes(prompt=prompt, metadata=probe.trace_metadata()),
    ):
        valid: list[RunObservation] = []
        last_error: Exception | None = None
        for index in range(1, config.runs + 1):
            try:
                valid.append(
                    _run_once(client, backend, text, images, config, slugs, index=index)
                )
            except (AggregateError, ValidationError, JsonSchemaError) as error:
                last_error = error

        if len(valid) < config.min_valid_runs:
            span.update(
                output={"valid_runs": len(valid), "runs": config.runs},
                level="ERROR",
                status_message=str(last_error),
            )
            raise AssessmentRunError(config.runs, len(valid), last_error)

        verdict = aggregate_observations(valid, slugs)
        span.update(
            output={
                "health_status": verdict.health_status,
                "observations": verdict.observations,
                "consensus": verdict.consensus,
                "fired_probes": [r["slug"] for r in verdict.probe_results if r["fired"]],
            }
        )
        trace_id: str | None = span.trace_id

    return replace(verdict, trace_id=trace_id)


def _asked_slugs(prompt: Any, probe: ProbeInput) -> tuple[str, ...]:
    """The checks this run will actually ask, and so vote on, in the order asked.

    A prompt version without ``{{probe_checks}}`` never shows the checks; voting
    on them would record every one as ``cannot_tell``, so none are recorded.
    """
    if not probe.checks:
        return ()
    variables = getattr(prompt, "variables", None)
    if variables is not None and "probe_checks" not in variables:
        logger.warning(
            "prompt %s v%s has no {{probe_checks}}; %d approved check(s) not asked",
            prompts.PROBE_PROMPT,
            getattr(prompt, "version", "?"),
            len(probe.checks),
        )
        return ()
    return tuple(check.slug for check in probe.checks)


def _run_once(
    client: Langfuse,
    backend: VLMBackend,
    text: str,
    images: list[bytes],
    config: ProbeConfig,
    slugs: tuple[str, ...],
    *,
    index: int,
) -> RunObservation:
    """One run: generate, validate, and on failure one repair with the errors fed back."""
    raw = _generate(client, backend, text, images, config, name=f"vlm-run-{index}")
    try:
        parsed = _parse(raw, slugs)
        return parsed
    except (AggregateError, ValidationError, JsonSchemaError) as error:
        repair = _repair_prompt(text, raw, error)
    raw = _generate(client, backend, repair, images, config, name=f"vlm-run-{index}-repair")
    parsed = _parse(raw, slugs)
    return parsed


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


def _parse(raw: str, slugs: tuple[str, ...] = ()) -> RunObservation:
    data = prepare_observation_json(extract_json_object(raw))
    findings = data.get("probe_findings")
    if isinstance(findings, list):
        allowed = set(slugs)
        seen: set[str] = set()
        kept = []
        for finding in findings:
            if not isinstance(finding, dict):
                kept.append(finding)
                continue
            slug = finding.get("slug")
            if not isinstance(slug, str) or slug not in allowed or slug in seen:
                continue
            seen.add(slug)
            kept.append(finding)
        data["probe_findings"] = kept
    try:
        validate_observation_json(data)
    except JsonSchemaError as error:
        where = getattr(error, "json_path", "") or "$"
        raise AggregateError(f"{where}: {error.message}") from error
    run = RunObservation.model_validate(data)
    try:
        run.check_probe_slugs(frozenset(slugs))
    except ValueError as error:
        raise AggregateError(str(error)) from error
    return run


_PLACEHOLDER: Final[re.Pattern[str]] = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _compile_prompt(prompt: Any, variables: dict[str, str]) -> str:
    """Compile ``variables``, dropping any the prompt template does not declare.

    An older prompt version must still compile. When the prompt shape is
    unknown, pass every variable.
    """
    declared = _declared_variables(prompt)
    if declared:
        variables = {key: value for key, value in variables.items() if key in declared}
    return prompt.compile(**variables)


def _declared_variables(prompt: Any) -> set[str]:
    names: set[str] = set()
    raw_vars = getattr(prompt, "variables", None)
    if isinstance(raw_vars, (list, tuple, set, frozenset)):
        names.update(str(item) for item in raw_vars)
    text = getattr(prompt, "prompt", None)
    if isinstance(text, str):
        names.update(_PLACEHOLDER.findall(text))
    return names


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

