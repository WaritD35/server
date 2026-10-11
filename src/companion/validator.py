"""Fact-preservation validator for the Companion Layer."""

from __future__ import annotations

import re

from advice.schemas import CarePlanRead


def extract_keywords(text: str) -> list[str]:
    """Extract significant keywords (excluding common stop words and filler words) from text."""
    stop_words = {
        "the", "a", "an", "and", "or", "to", "in", "on", "at", "by", "for",
        "with", "about", "against", "between", "into", "through", "during",
        "before", "after", "above", "below", "from", "up", "down", "is", "are",
        "was", "were", "be", "been", "being", "have", "has", "had", "do", "does",
        "did", "shall", "will", "should", "would", "may", "might", "must", "can",
        "could", "it", "its", "you", "your", "we", "our", "plant", "please",
        "carefully", "ensure", "receives", "based", "current", "level", "condition",
    }
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    return [w for w in words if w not in stop_words]


def validate_fact_preservation(
    care_plan: CarePlanRead,
    companion_text: str,
) -> tuple[bool, list[str]]:
    """Verify that companion_text mentions every action from care_plan.

    Returns (is_valid, list_of_missing_actions).
    """
    text_lower = companion_text.lower()
    missing_actions: list[str] = []

    for item in care_plan.actions:
        action_text = item.action
        keywords = extract_keywords(action_text)

        if not keywords:
            # If action has no keywords, check literal substring
            if action_text.lower() not in text_lower:
                missing_actions.append(action_text)
            continue

        # Check that at least 34% (or at least 2 keywords) appear in companion text
        matches = [kw for kw in keywords if kw in text_lower]
        coverage = len(matches) / len(keywords)

        if coverage < 0.34 and len(matches) < 2:
            missing_actions.append(action_text)

    is_valid = len(missing_actions) == 0
    return is_valid, missing_actions
