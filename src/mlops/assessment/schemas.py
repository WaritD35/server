"""The contract for one VLM run's answer.

The probe prompt (in Langfuse) asks for exactly this JSON object, and
:mod:`mlops.assessment.runtime` validates every run against it before the run is
allowed into the majority vote. A run that fails is sent back once as a repair;
the aggregate built from the valid runs is our own code's output and is not
validated again.
"""

from __future__ import annotations

from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, field_validator

StressLevel = Literal["none", "mild", "moderate", "severe", "cannot_tell"]

#: What ``assessment.service.SEVERITY_RANKS`` ranks — nothing else may reach it.
Severity = Literal["mild", "moderate", "severe"]

ObservationType = Literal[
    "leaf_yellowing",
    "brown_edges",
    "drooping",
    "curling",
    "visible_damage",
    "discoloration",
    "other",
]

_STRICT = ConfigDict(extra="forbid")


def _normalized(value: Any) -> Any:
    # "Mild " is the model being sloppy, not wrong; reject only real mismatches.
    return value.strip().lower() if isinstance(value, str) else value


class ImageObservation(BaseModel):
    """What the model saw in one of the two frames."""

    model_config = _STRICT

    image_description: str
    leaf_posture: str
    leaf_color: str
    visible_damage: str
    visible_stress_level: StressLevel

    @field_validator("visible_stress_level", mode="before")
    @classmethod
    def _normalize_stress(cls, value: Any) -> Any:
        return _normalized(value)


class CurrentObservation(BaseModel):
    """One visible concern in the current frame (Image 2)."""

    model_config = _STRICT

    type: ObservationType
    severity: Severity
    description: str = Field(min_length=1)

    @field_validator("type", "severity", mode="before")
    @classmethod
    def _normalize_labels(cls, value: Any) -> Any:
        return _normalized(value)


ProbeAnswer = Literal["yes", "no", "cannot_tell"]


class ProbeFinding(BaseModel):
    """The answer to one species-specific check (an approved ``knowledge`` probe)."""

    model_config = _STRICT

    #: The check's slug exactly as the prompt listed it.
    slug: str = Field(min_length=1)
    #: ``yes`` = Image 2 looks like the check's "worse" description.
    answer: ProbeAnswer
    evidence: str = ""

    @field_validator("slug", mode="before")
    @classmethod
    def _strip_slug(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("answer", mode="before")
    @classmethod
    def _normalize_answer(cls, value: Any) -> Any:
        return _normalized(value)


class RunObservation(BaseModel):
    """One run's full answer about the previous/current frame pair."""

    model_config = _STRICT

    image_1_observation: ImageObservation
    image_2_observation: ImageObservation
    visible_differences: list[str]
    current_observations: list[CurrentObservation]
    comparison_summary: str
    model_confidence: float = Field(ge=0.0, le=1.0)
    #: One entry per check the prompt listed; empty when it listed none. A prompt
    #: version without the checks section never asks for it, hence the default.
    probe_findings: list[ProbeFinding] = Field(default_factory=list)

    def check_probe_slugs(self, slugs: frozenset[str]) -> None:
        """Reject findings for checks that were not asked, or answered twice.

        A missing finding is allowed (it counts as ``cannot_tell``); an invented
        or duplicated slug means the model did not follow the list, so the run
        goes back for a repair.
        """
        seen: set[str] = set()
        for finding in self.probe_findings:
            if finding.slug not in slugs:
                raise ValueError(f"probe_findings: unknown slug {finding.slug!r}")
            if finding.slug in seen:
                raise ValueError(f"probe_findings: slug {finding.slug!r} answered twice")
            seen.add(finding.slug)


#: JSON Schema for one VLM run. Built from :class:`RunObservation` so the
#: validator and the model cannot drift.
OBSERVATION_JSON_SCHEMA: dict[str, Any] = RunObservation.model_json_schema()
_OBSERVATION_VALIDATOR = Draft202012Validator(OBSERVATION_JSON_SCHEMA)


def prepare_observation_json(data: dict[str, Any]) -> dict[str, Any]:
    """Copy ``data`` with the label casing the schema enums accept.

    The model often returns ``"Mild"`` or ``"Cannot_Tell"``. The schema enums
    are lowercase, matching the values stored after validation.
    """
    prepared = dict(data)
    for key in ("image_1_observation", "image_2_observation"):
        block = prepared.get(key)
        if isinstance(block, dict):
            prepared[key] = _lower_fields(block, "visible_stress_level")
    observations = prepared.get("current_observations")
    if isinstance(observations, list):
        prepared["current_observations"] = [_prepare_observation(item) for item in observations]
    findings = prepared.get("probe_findings")
    if isinstance(findings, list):
        prepared["probe_findings"] = [
            _prepare_finding(item) if isinstance(item, dict) else item for item in findings
        ]
    return prepared


def validate_observation_json(data: dict[str, Any]) -> None:
    """Raise ``jsonschema.ValidationError`` when ``data`` does not match the schema."""
    errors = sorted(_OBSERVATION_VALIDATOR.iter_errors(data), key=lambda error: error.json_path)
    if errors:
        raise errors[0]


def _lower_fields(item: dict[str, Any], *names: str) -> dict[str, Any]:
    copied = dict(item)
    for name in names:
        value = copied.get(name)
        if isinstance(value, str):
            copied[name] = value.strip().lower()
    return copied


def _prepare_finding(item: dict[str, Any]) -> dict[str, Any]:
    copied = _lower_fields(item, "answer")
    slug = copied.get("slug")
    if isinstance(slug, str):
        copied["slug"] = slug.strip()
    if copied.get("answer") not in ProbeAnswer.__args__:
        copied["answer"] = "cannot_tell"
    return copied


def _prepare_observation(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    copied = _lower_fields(item, "type", "severity")
    if copied.get("type") not in ObservationType.__args__:
        copied["type"] = "other"
    if copied.get("severity") not in Severity.__args__:
        copied["severity"] = "mild"
    return copied
