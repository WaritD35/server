"""The generation contract has to reject bad output, not just describe good output.

Every rule here is one the model has actually got wrong in the wild, and each
failure is what :mod:`mlops.knowledge.runtime` turns into a repair instruction —
so a rule that silently passes is a bug that reaches the reviewer as a plausible
looking, wrong probe.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from mlops.knowledge.schemas import (
    GeneratedProbeSet,
    UngroundedProbeError,
)

DOCUMENT = (
    "Spathiphyllum wallisii likes evenly moist soil.\n"
    "The leaves droop noticeably when it dries out, and recover within a day of "
    "a thorough watering.\n"
    "Direct midday sun scorches the leaf margins."
)


def action(**overrides: Any) -> dict[str, Any]:
    return {
        "instruction": "Water thoroughly until it drains from the bottom.",
        "urgency": "today",
        "expect_typical_hours": 12,
        "expect_max_hours": 48,
        "expected_signal": "leaf_recovery",
        **overrides,
    }


def probe(**overrides: Any) -> dict[str, Any]:
    return {
        "slug": "water_deficit.leaf_droop",
        "care_need": "water_deficit",
        "priority": 1,
        "is_screening": True,
        "question": "Are the leaves drooping or limp?",
        "worse_looks_like": "limp, folded, hanging leaves",
        "better_looks_like": "firm, upright leaves",
        "not_this": "natural night-time leaf folding",
        "evidence_quote": "The leaves droop noticeably when it dries out",
        "actions": [action()],
        **overrides,
    }


def probe_set(*probes: dict[str, Any]) -> dict[str, Any]:
    return {"probes": list(probes) or [probe()]}


def test_a_well_formed_set_validates_and_is_grounded() -> None:
    parsed = GeneratedProbeSet.model_validate(probe_set())
    parsed.validate_against_document(DOCUMENT)

    assert parsed.probes[0].slug == "water_deficit.leaf_droop"
    assert parsed.probes[0].actions[0].urgency == "today"


def test_quote_is_matched_across_rewrapped_lines() -> None:
    """The model re-wraps what it quotes; the words are what has to match."""
    parsed = GeneratedProbeSet.model_validate(
        probe_set(
            probe(
                evidence_quote=(
                    "The leaves   droop noticeably when it dries out, and\n"
                    "recover within a day"
                )
            )
        )
    )
    parsed.validate_against_document(DOCUMENT)


def test_a_mid_sentence_quote_may_come_back_capitalised() -> None:
    """Observed in a real run: the model quotes "...but direct midday sun..." as
    "Direct midday sun...", capitalising it as if it started a sentence. The
    words are right; only the typography moved. Rejecting that wasted three
    repair rounds on a correctly grounded probe.
    """
    parsed = GeneratedProbeSet.model_validate(
        probe_set(
            probe(evidence_quote="Direct midday sun scorches the leaf margins.")
        )
    )
    parsed.validate_against_document(
        "It tolerates shade, but direct midday sun scorches the leaf margins."
    )


def test_a_quote_the_document_does_not_contain_is_rejected() -> None:
    """The anti-hallucination check: an invented probe cites invented evidence."""
    parsed = GeneratedProbeSet.model_validate(
        probe_set(probe(evidence_quote="Mist the leaves twice a week in winter."))
    )
    with pytest.raises(UngroundedProbeError) as exc:
        parsed.validate_against_document(DOCUMENT)
    assert exc.value.slugs == ["water_deficit.leaf_droop"]


def test_extra_fields_are_rejected() -> None:
    """A field we do not store is a field the model invented."""
    with pytest.raises(ValidationError):
        GeneratedProbeSet.model_validate(probe_set(probe(confidence=0.9)))


@pytest.mark.parametrize(
    "slug",
    ["water_deficit", "Water_Deficit.leaf_droop", "water_deficit.", "水不足.droop"],
)
def test_malformed_slugs_are_rejected(slug: str) -> None:
    with pytest.raises(ValidationError):
        GeneratedProbeSet.model_validate(probe_set(probe(slug=slug)))


def test_slug_prefix_must_agree_with_care_need() -> None:
    """Both name the same condition; disagreeing means one of them is wrong."""
    with pytest.raises(ValidationError, match="care_need"):
        GeneratedProbeSet.model_validate(
            probe_set(probe(slug="light_excess.leaf_droop"))
        )


def test_duplicate_slugs_are_rejected() -> None:
    """``probe.slug`` is unique per set in the database — fail before the insert."""
    with pytest.raises(ValidationError, match="duplicate probe slugs"):
        GeneratedProbeSet.model_validate(
            probe_set(probe(priority=1), probe(priority=2))
        )


def test_duplicate_priorities_are_rejected() -> None:
    with pytest.raises(ValidationError, match="priorities must be unique"):
        GeneratedProbeSet.model_validate(
            probe_set(
                probe(),
                probe(slug="light_excess.leaf_scorch", care_need="light_excess"),
            )
        )


def test_a_set_with_no_screening_probe_is_rejected() -> None:
    with pytest.raises(ValidationError, match="is_screening"):
        GeneratedProbeSet.model_validate(probe_set(probe(is_screening=False)))


def test_a_probe_needs_at_least_one_action() -> None:
    """A probe nobody can act on has nothing to tell the plant owner."""
    with pytest.raises(ValidationError):
        GeneratedProbeSet.model_validate(probe_set(probe(actions=[])))


def test_grace_period_cannot_be_shorter_than_the_typical_wait() -> None:
    """``expect_max_hours`` is the re-alert grace period — inverted, it spams."""
    with pytest.raises(ValidationError, match="expect_max_hours"):
        GeneratedProbeSet.model_validate(
            probe_set(
                probe(
                    actions=[action(expect_typical_hours=48, expect_max_hours=12)]
                )
            )
        )


def test_an_empty_set_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GeneratedProbeSet.model_validate({"probes": []})
