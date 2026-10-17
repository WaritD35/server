"""Majority vote and observation merging over validated VLM runs.

Ported from PR #15's ``assessment.aggregate``: the per-run decision rules, the
cautious tie-break and the merge are unchanged. What changed is the input (a
validated :class:`RunObservation`, not a loose dict), the output (a
:class:`ProbeVerdict`) and the status name, which now matches
``assessment.schemas.HealthStatus``.
"""

from typing import Any, get_args

import pytest

from assessment.schemas import HealthStatus
from mlops.assessment.aggregate import (
    AggregateError,
    aggregate_observations,
    extract_json_object,
    observation_types_from_text,
)
from mlops.assessment.runtime import ProbeVerdict
from mlops.assessment.schemas import ObservationType, RunObservation

ALLOWED_SEVERITIES = {"mild", "moderate", "severe"}


def image(stress: str, **fields: str) -> dict[str, str]:
    return {
        "image_description": "A potted plant on a shelf.",
        "leaf_posture": "upright",
        "leaf_color": "even green",
        "visible_damage": "none visible",
        "visible_stress_level": stress,
        **fields,
    }


def run(
    stress: str,
    *,
    observations: list[dict[str, str]] | None = None,
    probe_findings: list[dict[str, str]] | None = None,
    confidence: float = 0.8,
    image_2: dict[str, str] | None = None,
) -> RunObservation:
    payload: dict[str, Any] = {
        "image_1_observation": image("none"),
        "image_2_observation": image_2 or image(stress),
        "visible_differences": [],
        "current_observations": observations or [],
        "comparison_summary": "Little has changed.",
        "model_confidence": confidence,
        "probe_findings": probe_findings or [],
    }
    return RunObservation.model_validate(payload)


def concern(type_: str, severity: str, description: str) -> dict[str, str]:
    return {"type": type_, "severity": severity, "description": description}


# --------------------------------------------------------------------------- #
# one run → one status
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("stress", "expected"),
    [
        ("none", "healthy"),
        ("mild", "possibly_unhealthy"),
        ("cannot_tell", "possibly_unhealthy"),
        ("moderate", "unhealthy"),
        ("severe", "unhealthy"),
    ],
)
def test_the_current_image_stress_level_decides_the_status(stress: str, expected: str) -> None:
    verdict = aggregate_observations([run(stress)])

    assert verdict.health_status == expected


def test_a_fired_approved_probe_prevents_a_healthy_verdict() -> None:
    verdict = aggregate_observations(
        [run("none", probe_findings=[{"slug": "water_deficit.leaf_droop", "answer": "yes"}])],
        ["water_deficit.leaf_droop"],
    )

    assert verdict.health_status == "possibly_unhealthy"


def test_all_approved_probes_answered_no_means_healthy() -> None:
    slugs = ["water_deficit.leaf_droop", "low_humidity.brown_tips"]
    findings = [{"slug": slug, "answer": "no"} for slug in slugs]

    verdict = aggregate_observations(
        [
            run(
                "severe",
                observations=[concern("brown_edges", "severe", "Brown edges.")],
                probe_findings=findings,
            )
        ],
        slugs,
    )

    assert verdict.health_status == "healthy"
    assert verdict.observations == []


def test_unanswered_approved_probe_is_not_treated_as_normal() -> None:
    verdict = aggregate_observations(
        [run("none", probe_findings=[{"slug": "water_deficit.leaf_droop", "answer": "no"}])],
        ["water_deficit.leaf_droop", "low_humidity.brown_tips"],
    )

    assert verdict.health_status == "possibly_unhealthy"


def test_the_status_is_spelled_the_way_the_assessment_context_spells_it() -> None:
    """PR #15 said `possible_unhealthy`; `HealthStatus` would reject it."""
    verdicts = [aggregate_observations([run(s)]) for s in ("none", "mild", "severe")]

    assert {HealthStatus(v.health_status) for v in verdicts} == set(HealthStatus)


def test_the_result_is_a_probe_verdict_with_no_trace_yet() -> None:
    verdict = aggregate_observations([run("none")])

    assert isinstance(verdict, ProbeVerdict)
    assert verdict.trace_id is None


# --------------------------------------------------------------------------- #
# many runs → majority, ties lean cautious
# --------------------------------------------------------------------------- #


def test_the_majority_status_wins_and_agreement_is_its_share() -> None:
    verdict = aggregate_observations(
        [run("none"), run("none"), run("none"), run("severe"), run("moderate")]
    )

    assert verdict.health_status == "healthy"
    assert verdict.consensus["agreement"] == 0.6
    assert verdict.consensus["runs"] == 5


@pytest.mark.parametrize(
    ("stresses", "expected"),
    [
        (("none", "severe"), "unhealthy"),
        (("none", "mild"), "possibly_unhealthy"),
        (("mild", "severe"), "unhealthy"),
        (("none", "mild", "severe"), "unhealthy"),
    ],
)
def test_a_tie_resolves_to_the_more_cautious_status(
    stresses: tuple[str, ...], expected: str
) -> None:
    verdict = aggregate_observations([run(s) for s in stresses])

    assert verdict.health_status == expected


def test_model_stated_average_is_the_mean_stated_confidence() -> None:
    verdict = aggregate_observations(
        [run("none", confidence=0.9), run("none", confidence=0.6), run("none", confidence=0.7)]
    )

    assert verdict.consensus["model_stated_average"] == pytest.approx(0.733)


def test_no_runs_is_an_error_not_a_default_verdict() -> None:
    with pytest.raises(AggregateError):
        aggregate_observations([])


# --------------------------------------------------------------------------- #
# observations
# --------------------------------------------------------------------------- #


def test_only_runs_that_agree_with_the_verdict_contribute_observations() -> None:
    verdict = aggregate_observations(
        [
            run("moderate", observations=[concern("drooping", "moderate", "Leaves hang down.")]),
            run("moderate", observations=[concern("drooping", "severe", "Leaves hang limp.")]),
            run("none", observations=[concern("curling", "mild", "One leaf curls.")]),
        ]
    )

    assert verdict.health_status == "unhealthy"
    assert [o["type"] for o in verdict.observations] == ["drooping"]


def test_the_same_type_is_merged_to_its_worst_severity_and_fullest_description() -> None:
    verdict = aggregate_observations(
        [
            run("moderate", observations=[concern("drooping", "mild", "Leaves hang.")]),
            run("moderate", observations=[concern("drooping", "severe", "Leaves hang limp.")]),
        ]
    )

    assert verdict.observations == [
        {"type": "drooping", "severity": "severe", "description": "Leaves hang limp."}
    ]


def test_observations_are_ordered_worst_first_then_by_type() -> None:
    verdict = aggregate_observations(
        [
            run(
                "moderate",
                observations=[
                    concern("curling", "mild", "One leaf rolls inward."),
                    concern("drooping", "moderate", "Several leaves hang."),
                    concern("brown_edges", "mild", "Brown along one edge."),
                ],
            )
        ]
    )

    assert [(o["severity"], o["type"]) for o in verdict.observations] == [
        ("moderate", "drooping"),
        ("mild", "brown_edges"),
        ("mild", "curling"),
    ]


def test_negated_or_partial_words_are_not_new_symptoms() -> None:
    assert observation_types_from_text("Leaves show no yellowing and are not dry.") == []
    assert observation_types_from_text("The pot is spotless and the leaves look yellowish.") == []
    assert observation_types_from_text("Leaves are not dry, but two leaves are yellowing.") == [
        "leaf_yellowing"
    ]


def test_a_description_naming_another_symptom_is_recorded_under_both_types() -> None:
    verdict = aggregate_observations(
        [
            run(
                "moderate",
                observations=[
                    concern("other", "moderate", "Yellow patches with brown tips on two leaves.")
                ],
            )
        ]
    )

    assert {o["type"] for o in verdict.observations} == {"other", "leaf_yellowing", "brown_edges"}


def test_a_healthy_current_image_reports_no_concerns() -> None:
    verdict = aggregate_observations([run("none")])

    assert verdict.observations == []


def test_a_stressed_image_with_no_listed_concern_falls_back_to_the_image_fields() -> None:
    verdict = aggregate_observations(
        [run("mild", image_2=image("mild", leaf_posture="slightly drooping"))]
    )

    assert verdict.health_status == "possibly_unhealthy"
    posture = [o for o in verdict.observations if o["type"] == "drooping"]
    assert posture == [
        {"type": "drooping", "severity": "mild", "description": "slightly drooping"}
    ]
    assert {o["type"] for o in verdict.observations} <= set(get_args(ObservationType))


@pytest.mark.parametrize("stress", ["none", "mild", "moderate", "severe", "cannot_tell"])
def test_every_output_severity_is_one_the_rules_engine_ranks(stress: str) -> None:
    """`assessment.service.SEVERITY_RANKS` knows mild, moderate and severe only."""
    verdict = aggregate_observations(
        [
            run(stress),
            run(stress, observations=[concern("drooping", "moderate", "Leaves hang.")]),
        ]
    )

    assert {o["severity"] for o in verdict.observations} <= ALLOWED_SEVERITIES


# --------------------------------------------------------------------------- #
# pulling the JSON object out of the raw model text
# --------------------------------------------------------------------------- #


def test_a_fenced_json_answer_is_unwrapped() -> None:
    assert extract_json_object('Here:\n```json\n{"a": 1}\n```\nDone.') == {"a": 1}


def test_prose_around_a_bare_object_is_ignored() -> None:
    assert extract_json_object('Sure! {"a": {"b": 2}} Hope that helps.') == {"a": {"b": 2}}


@pytest.mark.parametrize("text", ["no json here", "{not json}", "[1, 2]"])
def test_text_without_one_json_object_is_an_error(text: str) -> None:
    with pytest.raises(AggregateError):
        extract_json_object(text)
