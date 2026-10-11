"""Majority vote over repeated VLM runs, and the verdict it produces.

Ported from PR #15 (``assessment.aggregate``). The decision rules are unchanged:
each run's status comes from the current frame's stress level, the majority
wins, ties lean cautious, and only the runs that agree with the verdict
contribute observations. Health statuses are spelled as
``assessment.schemas.HealthStatus`` spells them; severities are limited to what
``assessment.service.SEVERITY_RANKS`` ranks.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from mlops.assessment.schemas import RunObservation


class AggregateError(RuntimeError):
    """Error raised when VLM observations cannot be aggregated."""


@dataclass(frozen=True)
class ProbeVerdict:
    """What ``run_probe`` returns: the aggregate of the valid runs."""

    #: ``healthy`` | ``possibly_unhealthy`` | ``unhealthy``.
    health_status: str
    #: ``type``, ``severity`` (mild | moderate | severe) and ``description``.
    observations: list[dict[str, str]]
    #: ``agreement`` (share of runs behind the verdict), ``runs`` (valid runs
    #: aggregated) and ``model_stated_average`` (mean stated confidence or None).
    consensus: dict[str, float | int | None]
    #: Langfuse trace id, so the caller can correlate its own row with the run.
    trace_id: str | None = None


STRESS_SCORES: dict[str, int] = {
    "none": 0,
    "mild": 1,
    "moderate": 2,
    "severe": 3,
}

VALID_OBSERVATION_SEVERITIES = {"mild", "moderate", "severe"}
VALID_OBSERVATION_TYPES = {
    "leaf_yellowing",
    "brown_edges",
    "drooping",
    "curling",
    "visible_damage",
    "discoloration",
    "other",
}


@dataclass(frozen=True)
class RunDecision:
    """Derived health decision from one VLM observation run."""

    health_status: str
    observations: list[dict[str, str]]
    image_1_stress_level: str | None
    image_2_stress_level: str | None
    model_confidence: float | None
    raw_observation: dict[str, Any]


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from raw VLM text, including fenced JSON output."""

    stripped = text.strip()
    fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if fenced_match:
        stripped = fenced_match.group(1)
    else:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise AggregateError("VLM response does not contain a JSON object.")
        stripped = stripped[start : end + 1]

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as error:
        raise AggregateError(f"VLM response is not valid JSON: {error}") from error

    if not isinstance(parsed, dict):
        raise AggregateError("VLM response JSON must be an object.")
    return parsed


def normalize_stress_level(value: object) -> str | None:
    """Normalize one visible_stress_level value from the VLM output."""

    if not isinstance(value, str):
        return None

    normalized = value.strip().lower()
    if normalized in STRESS_SCORES or normalized == "cannot_tell":
        return normalized
    return None


def stress_score(stress_level: str | None) -> int | None:
    """Convert visible stress level to an ordinal score."""

    if stress_level is None or stress_level == "cannot_tell":
        return None
    return STRESS_SCORES.get(stress_level)


def clamp_model_confidence(value: object) -> float | None:
    """Normalize the model's own stated confidence when present."""

    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    return max(0.0, min(1.0, float(value)))


def observation_stress_level(observation: dict[str, Any], key: str) -> str | None:
    """Read visible_stress_level from one image observation object."""

    section = observation.get(key)
    if not isinstance(section, dict):
        return None
    return normalize_stress_level(section.get("visible_stress_level"))


def derive_run_decision(observation: dict[str, Any]) -> RunDecision:
    """Derive health status and current observations from one VLM run."""

    image_1_stress = observation_stress_level(observation, "image_1_observation")
    image_2_stress = observation_stress_level(observation, "image_2_observation")
    image_2_score = stress_score(image_2_stress)

    if image_2_score is None:
        health_status = "possibly_unhealthy"
    elif image_2_score == 0:
        health_status = "healthy"
    elif image_2_score == 1:
        health_status = "possibly_unhealthy"
    else:
        health_status = "unhealthy"

    return RunDecision(
        health_status=health_status,
        observations=clean_current_observations(observation, image_2_stress),
        image_1_stress_level=image_1_stress,
        image_2_stress_level=image_2_stress,
        model_confidence=clamp_model_confidence(observation.get("model_confidence")),
        raw_observation=observation,
    )


def majority_health_status(decisions: list[RunDecision]) -> str:
    """Return the winning health status, preferring caution on ties."""

    counts = Counter(decision.health_status for decision in decisions)
    if not counts:
        return "possibly_unhealthy"

    highest_count = max(counts.values())
    winners = [status for status, count in counts.items() if count == highest_count]
    if len(winners) == 1:
        return winners[0]

    tie_order = ["unhealthy", "possibly_unhealthy", "healthy"]
    for status in tie_order:
        if status in winners:
            return status
    return winners[0]


def clean_text_field(value: object) -> str:
    """Normalize optional VLM text fields for the compact output contract."""

    if isinstance(value, str) and value.strip():
        text = value.strip()
        text = re.sub(r"\b[Ll]eaves in image[_ ]?2\b", "Current plant leaves", text)
        text = re.sub(r"\b[Ii]mage[_ ]?2\b", "current plant state", text)
        return text
    return "cannot_tell"


def normalize_observation_type(value: object) -> str | None:
    """Normalize a VLM observation type into a compact snake_case label."""

    if not isinstance(value, str) or not value.strip():
        return None
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    if normalized == "yellowing":
        return "leaf_yellowing"
    if normalized in {"brown_tips", "browning_edges"}:
        return "brown_edges"
    if normalized in VALID_OBSERVATION_TYPES:
        return normalized
    return None


def _symptom_pattern(alternatives: str) -> re.Pattern[str]:
    """Match a whole symptom word that is not immediately negated."""

    return re.compile(rf"(?<!\bno\s)(?<!\bnot\s)\b(?:{alternatives})\b")


_SYMPTOM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("leaf_yellowing", _symptom_pattern(r"yellow|yellowing")),
    ("brown_edges", _symptom_pattern(r"brown|browning|brown_edges|brown_tips")),
    ("drooping", _symptom_pattern(r"droop|drooping|limp|collapsed")),
    ("curling", _symptom_pattern(r"curl|curling")),
    ("visible_damage", _symptom_pattern(r"damage|damaged|tear|tears|spot|spots|dry")),
    ("discoloration", _symptom_pattern(r"discolor|discoloration")),
)


def observation_types_from_text(*values: object) -> list[str]:
    """Extract known symptom types from VLM type and description text."""

    text = " ".join(value for value in values if isinstance(value, str)).lower()
    matches = [
        symptom_type
        for symptom_type, pattern in _SYMPTOM_PATTERNS
        if pattern.search(text)
    ]
    return list(dict.fromkeys(matches))


def normalize_observation_severity(value: object) -> str | None:
    """Normalize one per-observation severity value."""

    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in VALID_OBSERVATION_SEVERITIES:
        return normalized
    return None


def severity_from_stress_level(stress_level: str | None) -> str:
    """Map image-level visible stress into observation-level severity."""

    if stress_level in VALID_OBSERVATION_SEVERITIES:
        return stress_level
    if stress_level == "none":
        return "mild"
    return "moderate"


def clean_current_observations(
    observation: dict[str, Any],
    image_2_stress: str | None,
) -> list[dict[str, str]]:
    """Return Image 2 health-concern observations for the final output."""

    explicit_observations = observation.get("current_observations")
    if isinstance(explicit_observations, list):
        cleaned = []
        for item in explicit_observations:
            if not isinstance(item, dict):
                continue
            severity = normalize_observation_severity(item.get("severity"))
            description = clean_text_field(item.get("description"))
            if not severity or description == "cannot_tell":
                continue
            observation_types = observation_types_from_text(item.get("type"), description)
            observation_type = normalize_observation_type(item.get("type"))
            if observation_type and observation_type not in observation_types:
                observation_types.insert(0, observation_type)
            for observation_type in observation_types:
                cleaned.append(
                    {
                        "type": observation_type,
                        "severity": severity,
                        "description": description,
                    }
                )
        if cleaned or image_2_stress == "none":
            return cleaned

    section = observation.get("image_2_observation")
    if not isinstance(section, dict):
        section = {}

    if image_2_stress == "none":
        return []

    severity = severity_from_stress_level(image_2_stress)
    fallback: list[dict[str, str]] = []
    for observation_type, field in [
        ("drooping", "leaf_posture"),
        ("discoloration", "leaf_color"),
        ("visible_damage", "visible_damage"),
    ]:
        description = clean_text_field(section.get(field))
        if description != "cannot_tell":
            fallback.append(
                {
                    "type": observation_type,
                    "severity": severity,
                    "description": description,
                }
            )
    return fallback


def severity_rank(severity: str) -> int:
    """Sort observation severities from mild to severe."""

    return {"mild": 1, "moderate": 2, "severe": 3}.get(severity, 0)


def consensus_observations(decisions: list[RunDecision]) -> list[dict[str, str]]:
    """Merge per-run observations into one compact observation list."""

    grouped: dict[str, list[dict[str, str]]] = {}
    for decision in decisions:
        for observation in decision.observations:
            grouped.setdefault(observation["type"], []).append(observation)

    merged = []
    for observation_type, items in grouped.items():
        severity = max(
            (item["severity"] for item in items),
            key=severity_rank,
        )
        description = max(
            (item["description"] for item in items),
            key=len,
        )
        merged.append(
            {
                "type": observation_type,
                "severity": severity,
                "description": description,
            }
        )

    return sorted(
        merged,
        key=lambda item: (-severity_rank(item["severity"]), item["type"]),
    )


def aggregate_observations(runs: list[RunObservation]) -> ProbeVerdict:
    """Aggregate repeated, already-validated VLM runs into one verdict."""

    if not runs:
        raise AggregateError("At least one observation is required.")

    decisions = [derive_run_decision(run.model_dump()) for run in runs]
    health_status = majority_health_status(decisions)
    matching = [
        decision for decision in decisions if decision.health_status == health_status
    ]
    agreement = len(matching) / len(decisions)
    model_confidences = [
        decision.model_confidence
        for decision in decisions
        if decision.model_confidence is not None
    ]

    return ProbeVerdict(
        health_status=health_status,
        observations=consensus_observations(matching),
        consensus={
            "agreement": round(agreement, 3),
            "runs": len(decisions),
            "model_stated_average": (
                round(sum(model_confidences) / len(model_confidences), 3)
                if model_confidences
                else None
            ),
        },
    )
