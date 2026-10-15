"""The output contract for probe generation — and the validation of it.

Owner: TODO

These models are **the schema handed to the LLM** (as a JSON schema, via
``with_structured_output``) *and* the gate every generated set must pass before
``knowledge`` is allowed to see it. Both roles matter: the first makes
well-formed output likely, the second makes it certain.

Deliberately Pydantic and nothing else — ``mlops`` must not import a bounded
context, so these are plain models that mirror the *shape* ``knowledge`` stores
without depending on its ORM. The calling context maps them onto its own rows.

Three layers of checking, weakest to strongest:

1. **JSON schema** — the model is constrained to the field names and types.
2. **Field / model validators here** — the domain rules the JSON schema cannot
   express: slug format, slug ↔ ``care_need`` agreement, unique slugs, the
   ``expect_typical_hours <= expect_max_hours`` ordering.
3. :func:`GeneratedProbeSet.validate_against_document` — grounding. Every
   ``evidence_quote`` must actually occur in the research document. This is the
   anti-hallucination check, and it needs the source text, so it cannot live in
   a validator.

Anything the model gets wrong here comes back as a ``ValidationError`` that
:mod:`mlops.knowledge.runtime` feeds to it as a repair instruction.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: How soon an action has to happen. Mirrors ``probe_action.urgency``.
Urgency = Literal["today", "this_week", "this_month"]

#: ``water_deficit.leaf_droop`` — ``<care_need>.<observation>``.
SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")

#: An identifier segment: ``water_deficit``, ``light_excess``, ….
NAME_PATTERN = r"^[a-z][a-z0-9_]*$"

#: A month, in hours. An action whose effect takes longer than this is not
#: something a daily-cadence assessment loop can verify.
MAX_EXPECT_HOURS = 720

_WHITESPACE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    """Fold the differences that carry no meaning, for quote matching.

    Whitespace, because the model re-wraps what it quotes. Case, because a span
    lifted from the middle of a sentence comes back capitalised as if it were
    the start of one — the words are right and only the typography moved, which
    is not what this check is about.
    """
    return _WHITESPACE.sub(" ", text).strip().casefold()


class GeneratedAction(BaseModel):
    """One remedial action for a probe that has fired."""

    model_config = ConfigDict(extra="forbid")

    instruction: Annotated[str, Field(min_length=4, max_length=400)]
    urgency: Urgency
    #: When the effect usually becomes visible.
    expect_typical_hours: Annotated[
        int,
        Field(
            ge=1,
            le=MAX_EXPECT_HOURS,
            description=(
                "Hours after the action until its effect is usually visible in a photo."
            ),
        ),
    ]
    #: The grace period: no re-alert until this has elapsed. The ordering rule
    #: below cannot be expressed in JSON Schema, so the description states it.
    expect_max_hours: Annotated[
        int,
        Field(
            ge=1,
            le=MAX_EXPECT_HOURS,
            description=(
                "Grace period in hours: the latest point by which the effect should "
                "be visible; no new alert is raised before it. Must be greater than "
                f"or equal to expect_typical_hours, and at most {MAX_EXPECT_HOURS}."
            ),
        ),
    ]
    #: What recovery looks like, as a snake_case tag (``leaf_recovery``).
    expected_signal: Annotated[str | None, Field(pattern=NAME_PATTERN)] = None

    @model_validator(mode="after")
    def _hours_are_ordered(self) -> GeneratedAction:
        if self.expect_max_hours < self.expect_typical_hours:
            raise ValueError(
                "expect_max_hours must be >= expect_typical_hours "
                f"(got {self.expect_max_hours} < {self.expect_typical_hours})"
            )
        return self


class GeneratedProbe(BaseModel):
    """One single-question check the VLM will later be asked."""

    model_config = ConfigDict(extra="forbid")

    #: ``<care_need>.<observation>``, unique within the set.
    slug: str
    #: The condition being checked for: ``water_deficit``, ``light_excess``, ….
    care_need: Annotated[str, Field(pattern=NAME_PATTERN)]
    #: 1 = check first. Unique within the set.
    priority: Annotated[int, Field(ge=1)]
    #: Cheap, broad check — run before the rest to decide whether to go on.
    is_screening: bool
    #: Answerable from one whole-plant image alone, with no reference to another
    #: probe. There is no region to point at: assessment judges from the whole
    #: frame, so a probe that needs a close-up is a probe we cannot run.
    question: Annotated[str, Field(min_length=8, max_length=300)]
    worse_looks_like: Annotated[str, Field(min_length=4, max_length=300)]
    better_looks_like: Annotated[str, Field(min_length=4, max_length=300)]
    #: The over-detection guard: what resembles this but is not it.
    not_this: Annotated[str, Field(min_length=4, max_length=300)]
    #: Verbatim span of the research document this probe rests on. Required —
    #: a probe with no support in the source text is exactly what we reject.
    evidence_quote: Annotated[str, Field(min_length=8, max_length=1000)]

    actions: Annotated[list[GeneratedAction], Field(min_length=1, max_length=5)]

    @field_validator("slug")
    @classmethod
    def _slug_is_well_formed(cls, value: str) -> str:
        if not SLUG_PATTERN.match(value):
            raise ValueError(
                f"slug must be '<care_need>.<observation>' in snake_case, got {value!r}"
            )
        return value

    @model_validator(mode="after")
    def _slug_matches_care_need(self) -> GeneratedProbe:
        prefix = self.slug.split(".", 1)[0]
        if prefix != self.care_need:
            raise ValueError(
                f"slug prefix {prefix!r} must equal care_need {self.care_need!r}"
            )
        return self


class GeneratedProbeSet(BaseModel):
    """One generation run's full output, before any human has looked at it."""

    model_config = ConfigDict(extra="forbid")

    probes: Annotated[list[GeneratedProbe], Field(min_length=1, max_length=12)]

    @model_validator(mode="after")
    def _identifiers_are_unique(self) -> GeneratedProbeSet:
        slugs = [p.slug for p in self.probes]
        if len(set(slugs)) != len(slugs):
            duplicates = sorted({s for s in slugs if slugs.count(s) > 1})
            raise ValueError(f"duplicate probe slugs: {', '.join(duplicates)}")
        priorities = [p.priority for p in self.probes]
        if len(set(priorities)) != len(priorities):
            raise ValueError(f"probe priorities must be unique, got {priorities}")
        return self

    @model_validator(mode="after")
    def _has_a_screening_probe(self) -> GeneratedProbeSet:
        if not any(p.is_screening for p in self.probes):
            raise ValueError(
                "at least one probe must have is_screening=true — assessment runs "
                "the screening probes first to decide whether to run the rest"
            )
        return self

    def validate_against_document(self, body: str) -> None:
        """Check every ``evidence_quote`` really occurs in ``body``.

        Whitespace and case are normalised on both sides (see :func:`_normalise`),
        but the wording itself has to match: a quote the document does not
        contain means the probe was invented rather than derived. The quote is
        kept exactly as the model wrote it — only the comparison is relaxed.

        Raises:
            UngroundedProbeError: naming the probes that failed.
        """
        haystack = _normalise(body)
        ungrounded = [
            probe.slug
            for probe in self.probes
            if _normalise(probe.evidence_quote) not in haystack
        ]
        if ungrounded:
            raise UngroundedProbeError(ungrounded)


class UngroundedProbeError(ValueError):
    """Raised when an ``evidence_quote`` does not occur in the research document."""

    def __init__(self, slugs: list[str]) -> None:
        self.slugs = slugs
        super().__init__(
            "evidence_quote not found verbatim in the research document for: "
            + ", ".join(slugs)
        )
