"""``probe_set`` / ``probe`` — the LLM output.

``probe_set`` is one generation run: it records which research document was fed
in, which model produced it, and the human approval that follows. Each ``probe``
is a single-question probe the assessment pipeline later runs against the VLM.

A species has **at most one approved set at a time** — a partial unique index
enforces it, and :func:`knowledge.service.approve_probe_set` archives the
incumbent as part of approving its replacement. Archived sets are kept, so an
older one can be swapped back in by approving it again.

``species_code`` is denormalised from the research document so that uniqueness
can be a database constraint; the composite foreign key back to
``research_document (id, species_code)`` stops the two from drifting apart.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from knowledge.db import KNOWLEDGE_SCHEMA, Base, utcnow

if TYPE_CHECKING:
    from knowledge.models.action import ProbeAction
    from knowledge.models.document import ResearchDocument

#: One approved set per species; drafts and archived sets are unconstrained.
_APPROVED_ONLY = text("status = 'approved'")


class ProbeSet(Base):
    __tablename__ = "probe_set"
    __table_args__ = (
        ForeignKeyConstraint(
            ["research_document_id", "species_code"],
            [
                f"{KNOWLEDGE_SCHEMA}.research_document.id",
                f"{KNOWLEDGE_SCHEMA}.research_document.species_code",
            ],
            name="fk_probe_set_research_document",
        ),
        Index(
            "uq_probe_set_approved_species",
            "species_code",
            unique=True,
            postgresql_where=_APPROVED_ONLY,
            sqlite_where=_APPROVED_ONLY,
        ),
        {"schema": KNOWLEDGE_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    research_document_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # Denormalised from the document above (kept honest by the composite FK).
    species_code: Mapped[str] = mapped_column(Text, nullable=False)
    # Hash of research_document.body at generation time (see the freshness view).
    source_content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    llm_model: Mapped[str] = mapped_column(Text, nullable=False)  # 'gpt-4o-2024-11-20'
    prompt_version: Mapped[str | None] = mapped_column(Text)
    # 'draft' | 'approved' | 'archived'
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    approved_by: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A draft a reviewer turned down. Symmetric with the two fields above: the
    # rejection is as much a record as the approval, and ``note`` says why.
    rejected_by: Mapped[str | None] = mapped_column(Text)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    # The Langfuse trace of the generation run behind this set — the one link
    # between a row here and the prompt version, model output and repair rounds
    # that produced it. ``mlops`` keeps no copy of our ids, so this is the join.
    langfuse_trace_id: Mapped[str | None] = mapped_column(Text)

    research_document: Mapped["ResearchDocument"] = relationship(
        back_populates="probe_sets"
    )
    probes: Mapped[list["Probe"]] = relationship(
        back_populates="probe_set",
        cascade="all, delete-orphan",
        order_by="Probe.priority",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<ProbeSet {self.id} {self.status}>"


class Probe(Base):
    __tablename__ = "probe"
    __table_args__ = (
        UniqueConstraint("probe_set_id", "slug", name="uq_probe_set_slug"),
        {"schema": KNOWLEDGE_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    probe_set_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey(f"{KNOWLEDGE_SCHEMA}.probe_set.id"), nullable=False
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False)  # 'water_deficit.leaf_droop'
    care_need: Mapped[str] = mapped_column(Text, nullable=False)  # 'water_deficit'
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    is_screening: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    worse_looks_like: Mapped[str] = mapped_column(Text, nullable=False)
    better_looks_like: Mapped[str] = mapped_column(Text, nullable=False)
    not_this: Mapped[str] = mapped_column(Text, nullable=False)  # over-detection guard
    # The span of research_document.body this probe was generated from.
    evidence_quote: Mapped[str | None] = mapped_column(Text)

    probe_set: Mapped["ProbeSet"] = relationship(back_populates="probes")
    actions: Mapped[list["ProbeAction"]] = relationship(
        back_populates="probe",
        cascade="all, delete-orphan",
        order_by="ProbeAction.ordering",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Probe {self.slug!r}>"
