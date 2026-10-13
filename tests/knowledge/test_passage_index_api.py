"""Approve, archive, and reindex keep passages tied to the approved document."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from knowledge.models import KnowledgeChunk

SeedFn = Callable[..., object]


def _count(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(KnowledgeChunk)) or 0)


def test_approve_stays_approved_when_embedding_fails_and_drops_old_chunks(
    client: TestClient, session_factory: sessionmaker[Session], seed_full: SeedFn
) -> None:
    with session_factory() as session:
        document, live, _probe = seed_full(session, status="approved")
        _document, challenger, _other = seed_full(
            session, status="draft", slug="light_deficit.pale_leaves"
        )
        session.add(
            KnowledgeChunk(
                species_code="spath",
                topic="old",
                content="previous document",
                research_document_id=document.id,
                probe_set_id=live.id,
                source_content_hash=document.content_hash,
            )
        )
        session.commit()
        challenger_id = str(challenger.id)

    promoted = client.post(f"/knowledge/probe-sets/{challenger_id}/approve")
    assert promoted.status_code == 200
    assert promoted.json()["status"] == "approved"

    with session_factory() as session:
        assert _count(session) == 0


def test_archive_deletes_chunks_and_reject_does_not(
    client: TestClient, session_factory: sessionmaker[Session], seed_full: SeedFn
) -> None:
    with session_factory() as session:
        document, approved, _probe = seed_full(session, status="approved")
        _document, draft, _other = seed_full(session, status="draft", slug="light.pale")
        session.add(
            KnowledgeChunk(
                species_code="spath",
                topic="care",
                content="moist soil",
                research_document_id=document.id,
                probe_set_id=approved.id,
                source_content_hash=document.content_hash,
            )
        )
        session.commit()
        approved_id, draft_id = str(approved.id), str(draft.id)

    rejected = client.post(
        f"/knowledge/probe-sets/{draft_id}/reject", json={"comment": "not specific enough"}
    )
    assert rejected.status_code == 200
    with session_factory() as session:
        assert _count(session) == 1

    archived = client.post(f"/knowledge/probe-sets/{approved_id}/archive")
    assert archived.status_code == 200
    with session_factory() as session:
        assert _count(session) == 0


def test_reindex_404_without_an_approved_set(client: TestClient, add_species) -> None:
    add_species("spath")
    missing = client.post("/knowledge/species/spath/passages/reindex")
    assert missing.status_code == 404


def test_reindex_reports_embedding_failure(
    client: TestClient, session_factory: sessionmaker[Session], seed_full: SeedFn
) -> None:
    with session_factory() as session:
        seed_full(session, status="approved")

    response = client.post("/knowledge/species/spath/passages/reindex")
    assert response.status_code == 200
    body = response.json()
    assert body["indexed"] is False
    assert body["passage_count"] == 0
    assert body["error"]
    assert body["species_code"] == "spath"
