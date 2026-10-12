"""The contract for one VLM run's answer.

The probe prompt (in Langfuse) asks for exactly this JSON object, and
:mod:`mlops.assessment.runtime` validates every run against it before the run is
allowed into the majority vote. A run that fails is sent back once as a repair;
the aggregate built from the valid runs is our own code's output and is not
validated again.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

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


def _normalized(value: Any) -> Any:
    # "Mild " is the model being sloppy, not wrong; reject only real mismatches.
    return value.strip().lower() if isinstance(value, str) else value


class ImageObservation(BaseModel):
    """What the model saw in one of the two frames."""

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
