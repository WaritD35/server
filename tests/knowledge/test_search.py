"""Hybrid search over approved passages."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session, sessionmaker

from knowledge import interface
from knowledge.retrieval import (
    _Hit,
    reciprocal_rank_fusion,
    render_ranked_passages,
    search_approved_passages,
)


def test_reciprocal_rank_fusion_prefers_a_passage_in_both_lists() -> None:
    fused = reciprocal_rank_fusion([["b", "a"], ["a", "c"]], k=60)
    assert fused[0] == "a"
    assert fused.index("b") < fused.index("c")


def test_render_caps_count_and_characters_and_drops_other_sets() -> None:
    approved = uuid.uuid4()
    other = uuid.uuid4()
    hits = [
        _Hit(id="other", probe_set_id=other, topic="Skip", content="not this set"),
        *[
            _Hit(id=f"p{i}", probe_set_id=approved, topic="Care", content="x" * 400)
            for i in range(5)
        ],
    ]
    text = render_ranked_passages(
        hits,
        keyword_ids=["other", "p0", "p1", "p2", "p3", "p4"],
        vector_ids=["p0", "p1"],
        approved_probe_set_id=approved,
        limit=4,
        max_chars=1500,
    )
    assert "not this set" not in text
    assert text.count("### Care") <= 4
    assert len(text) <= 1500


def test_search_returns_empty_without_an_approved_set_or_a_query(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        assert search_approved_passages(session, "spath", "drooping leaves") == ""
        assert search_approved_passages(session, "spath", "   ") == ""


def test_search_does_not_call_get_care_knowledge(
    session_factory: sessionmaker[Session], seed_full, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("get_care_knowledge must not be called")

    monkeypatch.setattr(interface, "get_care_knowledge", _boom)
    with session_factory() as session:
        seed_full(session)
        assert search_approved_passages(session, "spath", "moist soil") == ""
