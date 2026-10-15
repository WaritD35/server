"""``/knowledge`` HTTP API tests."""

import uuid
from collections.abc import Callable
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from core.users.dependencies import get_current_active_user
from core.users.models import User

SeedFn = Callable[..., Any]

DOC = {
    "species_code": "spath",
    "title": "Spathiphyllum — watering",
    "body": "Keep the soil evenly moist.",
    "author": "alice",
}


def test_knowledge_api_requires_superuser(client: TestClient) -> None:
    app = cast(FastAPI, client.app)
    app.dependency_overrides[get_current_active_user] = lambda: User(
        id=uuid.uuid4(),
        email="viewer@example.com",
        name="Viewer",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
    )
    assert client.get("/knowledge/species").status_code == 403
    assert client.post(
        "/knowledge/species",
        json={"species_code": "x", "scientific_name": "y"},
    ).status_code == 403
    assert client.get("/knowledge/documents").status_code == 403


def test_species_registration(client: TestClient) -> None:
    created = client.post(
        "/knowledge/species",
        json={"species_code": "spath", "scientific_name": "Spathiphyllum wallisii"},
    )
    assert created.status_code == 201
    assert created.json()["species_code"] == "spath"

    # now a document for it is accepted
    assert client.post("/knowledge/documents", json=DOC).status_code == 201

    # duplicate species -> 409
    dup = client.post(
        "/knowledge/species",
        json={"species_code": "spath", "scientific_name": "x"},
    )
    assert dup.status_code == 409

    assert [s["species_code"] for s in client.get("/knowledge/species").json()] == [
        "spath"
    ]


def test_create_document_unknown_species_returns_404(client: TestClient) -> None:
    assert client.post("/knowledge/documents", json=DOC).status_code == 404


def test_document_is_append_only(
    client: TestClient, add_species: Callable[..., None]
) -> None:
    add_species("spath")
    created = client.post("/knowledge/documents", json=DOC)
    assert created.status_code == 201
    doc = created.json()
    assert doc["content_hash"] and doc["status"] == "active"
    assert doc["archived_at"] is None
    doc_id = doc["id"]

    assert client.get(f"/knowledge/documents/{doc_id}").json()["body"] == DOC["body"]

    # no edit and no delete: revising means adding a row
    assert client.patch(
        f"/knowledge/documents/{doc_id}", json={"body": "x"}
    ).status_code == 405
    assert client.delete(f"/knowledge/documents/{doc_id}").status_code == 405


def test_new_document_archives_the_previous_one(
    client: TestClient, add_species: Callable[..., None]
) -> None:
    add_species("spath")
    first_id = client.post("/knowledge/documents", json=DOC).json()["id"]
    revised = client.post(
        "/knowledge/documents",
        json={**DOC, "body": "Water when the top inch dries."},
    )
    assert revised.status_code == 201
    second = revised.json()
    assert second["status"] == "active"
    assert second["content_hash"] != client.get(
        f"/knowledge/documents/{first_id}"
    ).json()["content_hash"]

    superseded = client.get(f"/knowledge/documents/{first_id}").json()
    assert superseded["status"] == "archived"
    assert superseded["archived_at"] is not None
    assert superseded["body"] == DOC["body"]  # kept verbatim, not overwritten

    # both rows are listed; the filter narrows it to the current one
    assert len(client.get("/knowledge/documents").json()) == 2
    active = client.get("/knowledge/documents", params={"status": "active"}).json()
    assert [d["id"] for d in active] == [second["id"]]


def test_list_documents_filters_by_species(
    client: TestClient, add_species: Callable[..., None]
) -> None:
    add_species("spath", "ficus")
    client.post("/knowledge/documents", json=DOC)
    client.post("/knowledge/documents", json={**DOC, "species_code": "ficus"})

    only_ficus = client.get("/knowledge/documents", params={"species_code": "ficus"})
    assert [d["species_code"] for d in only_ficus.json()] == ["ficus"]


def test_probe_set_endpoints(
    client: TestClient, session_factory: sessionmaker[Session], seed_full: SeedFn
) -> None:
    with session_factory() as session:
        _, probe_set, _ = seed_full(session)
        set_id = str(probe_set.id)

    listing = client.get("/knowledge/species/spath/probe-sets").json()
    assert len(listing) == 1
    assert listing[0]["probe_count"] == 1
    assert listing[0]["is_stale"] is False
    assert listing[0]["species_code"] == "spath"

    detail = client.get(f"/knowledge/probe-sets/{set_id}").json()
    probe = detail["probes"][0]
    assert probe["slug"] == "water_deficit.leaf_droop"
    assert probe["actions"][0]["expect_max_hours"] == 48

    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/knowledge/probe-sets/{missing}").status_code == 404


def test_approve_then_archive_probe_set(
    client: TestClient, session_factory: sessionmaker[Session], seed_full: SeedFn
) -> None:
    with session_factory() as session:
        _, probe_set, _ = seed_full(session, status="draft")
        set_id = str(probe_set.id)

    # not visible through the interface while still a draft
    assert client.get("/knowledge/species/spath/probes").status_code == 404

    approved = client.post(f"/knowledge/probe-sets/{set_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_by"] == "ops@example.com"

    assert client.get("/knowledge/species/spath/probes").status_code == 200

    # approving again is a no-op error
    assert client.post(f"/knowledge/probe-sets/{set_id}/approve").status_code == 409

    archived = client.post(f"/knowledge/probe-sets/{set_id}/archive")
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert archived.json()["archived_at"] is not None

    # archived set drops out of the interface again
    assert client.get("/knowledge/species/spath/probes").status_code == 404


def test_approving_a_set_archives_the_incumbent(
    client: TestClient, session_factory: sessionmaker[Session], seed_full: SeedFn
) -> None:
    with session_factory() as session:
        _, live, _ = seed_full(session, status="approved")
        _, challenger, _ = seed_full(
            session, status="draft", slug="light_deficit.pale_leaves"
        )
        live_id, challenger_id = str(live.id), str(challenger.id)

    promoted = client.post(f"/knowledge/probe-sets/{challenger_id}/approve")
    assert promoted.status_code == 200
    assert promoted.json()["status"] == "approved"

    statuses = {
        s["id"]: s["status"]
        for s in client.get("/knowledge/species/spath/probe-sets").json()
    }
    assert statuses == {live_id: "archived", challenger_id: "approved"}

    bundle = client.get("/knowledge/species/spath/probes").json()
    assert bundle["probe_set_id"] == challenger_id


def test_archived_set_can_be_swapped_back_in(
    client: TestClient, session_factory: sessionmaker[Session], seed_full: SeedFn
) -> None:
    with session_factory() as session:
        _, old, _ = seed_full(session, status="approved")
        _, new, _ = seed_full(session, status="draft", slug="light_deficit.pale")
        old_id, new_id = str(old.id), str(new.id)

    client.post(f"/knowledge/probe-sets/{new_id}/approve")

    # roll back to the archived set: it takes over and the newer one is archived
    restored = client.post(f"/knowledge/probe-sets/{old_id}/approve")
    assert restored.status_code == 200
    assert restored.json()["status"] == "approved"
    assert restored.json()["archived_at"] is None

    statuses = {
        s["id"]: s["status"]
        for s in client.get("/knowledge/species/spath/probe-sets").json()
    }
    assert statuses == {old_id: "approved", new_id: "archived"}
    assert client.get("/knowledge/species/spath/probes").json()["probe_set_id"] == old_id


def test_archive_requires_an_approved_set(
    client: TestClient, session_factory: sessionmaker[Session], seed_full: SeedFn
) -> None:
    with session_factory() as session:
        _, draft, _ = seed_full(session, status="draft")
        set_id = str(draft.id)

    assert client.post(f"/knowledge/probe-sets/{set_id}/archive").status_code == 409


def test_approve_missing_probe_set_returns_404(client: TestClient) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.post(f"/knowledge/probe-sets/{missing}/approve").status_code == 404


def test_species_probes_bundle(
    client: TestClient, session_factory: sessionmaker[Session], seed_full: SeedFn
) -> None:
    assert client.get("/knowledge/species/spath/probes").status_code == 404

    with session_factory() as session:
        seed_full(session)

    bundle = client.get("/knowledge/species/spath/probes").json()
    assert bundle["species_code"] == "spath"
    assert bundle["is_stale"] is False
    assert bundle["probes"][0]["actions"][0]["urgency"] == "today"


def test_bundle_goes_stale_when_the_research_is_revised(
    client: TestClient, session_factory: sessionmaker[Session], seed_full: SeedFn
) -> None:
    with session_factory() as session:
        seed_full(session)

    assert client.get("/knowledge/species/spath/probes").json()["is_stale"] is False

    client.post(
        "/knowledge/documents",
        json={**DOC, "title": "revised", "body": "Revised guidance."},
    )

    assert client.get("/knowledge/species/spath/probes").json()["is_stale"] is True
