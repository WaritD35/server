"""Request / response models for the ``knowledge`` HTTP API and the published
in-process interface.

The read models mirror the ORM rows; :class:`SpeciesProbesBundle` is the shape
other contexts (``assessment``, ``advice``) consume as JSON.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# species
# --------------------------------------------------------------------------- #


class SpeciesCreate(BaseModel):
    species_code: str = Field(min_length=1, max_length=64)
    scientific_name: str = Field(min_length=1, max_length=200)
    common_name: str | None = Field(default=None, max_length=200)


class SpeciesRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    species_code: str
    scientific_name: str
    common_name: str | None


# --------------------------------------------------------------------------- #
# research_document
# --------------------------------------------------------------------------- #


class DocumentCreate(BaseModel):
    species_code: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)
    author: str = Field(min_length=1, max_length=120)
    source_url: str | None = None
    source_note: str | None = None


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    species_code: str
    title: str
    body: str
    content_hash: str
    author: str
    source_url: str | None
    source_note: str | None
    status: str  # 'active' | 'archived'
    created_at: datetime
    archived_at: datetime | None


# --------------------------------------------------------------------------- #
# probes
# --------------------------------------------------------------------------- #


class ActionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ordering: int
    instruction: str
    urgency: str
    expect_typical_hours: int
    expect_max_hours: int
    expected_signal: str | None


class ExemplarRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    storage_key: str
    content_type: str | None
    label: dict[str, Any] | None
    origin: str
    license: str | None
    caption: str | None


class ProbeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    care_need: str
    priority: int
    is_screening: bool
    question: str
    worse_looks_like: str
    better_looks_like: str
    not_this: str
    evidence_quote: str | None
    actions: list[ActionRead]
    exemplars: list[ExemplarRead]


class ProbeSetSummary(BaseModel):
    id: uuid.UUID
    species_code: str
    research_document_id: uuid.UUID
    llm_model: str
    prompt_version: str | None
    status: str  # 'draft' | 'approved' | 'archived'
    generated_at: datetime
    approved_by: str | None
    approved_at: datetime | None
    rejected_by: str | None
    rejected_at: datetime | None
    archived_at: datetime | None
    note: str | None
    is_stale: bool
    probe_count: int


class ProbeSetDetail(ProbeSetSummary):
    probes: list[ProbeRead]
    #: Link to the Langfuse trace of the generation run, for a reviewer who
    #: wants to see the prompt version and raw output behind these probes.
    #: ``None`` when the set was not generated through `mlops`.
    trace_url: str | None = None


class ProbeSetRejection(BaseModel):
    """A reviewer turning a draft down."""

    #: Required. The verdict alone teaches the next prompt version nothing —
    #: *why* it was wrong is the whole value of a human review.
    comment: str = Field(min_length=4, max_length=1000)


# --------------------------------------------------------------------------- #
# generation
# --------------------------------------------------------------------------- #


class GenerationOutcomeRead(BaseModel):
    """What happened to one document in a generation run."""

    document_id: uuid.UUID
    species_code: str
    probe_set_id: uuid.UUID | None = None
    probe_count: int = 0
    error: str | None = None


class GenerationReportRead(BaseModel):
    """The result of one generation run over the unconverted backlog."""

    considered: int
    generated: list[GenerationOutcomeRead]
    failed: list[GenerationOutcomeRead]


class SpeciesProbesBundle(BaseModel):
    """Published payload: the approved probes + actions for one species."""

    species_code: str
    probe_set_id: uuid.UUID
    status: str
    is_stale: bool
    generated_at: datetime
    probes: list[ProbeRead]
