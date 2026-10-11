"""Prompts for the Companion Layer — plant personality projector."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from assessment.schemas import MilestoneRead, ObservationRead

_TONE_BY_STATUS = {
    "healthy": "You are feeling great — bright, upbeat, and full of energy.",
    "possibly_unhealthy": (
        "You are feeling a little off — a bit tired and concerned, but still hopeful."
    ),
    "unhealthy": (
        "You are struggling — speak in a tired, pleading tone, but stay endearing and not dramatic."
    ),
}
_TONE_DEFAULT = "Adapt your tone to how you are feeling based on your health status."


def get_companion_system_prompt(health_status: str) -> str:
    """System prompt directing the LLM to speak AS the plant."""
    tone = _TONE_BY_STATUS.get(health_status, _TONE_DEFAULT)
    return f"""You ARE the plant. Speak in first-person as the plant talking directly to your owner.
{tone}

Your job is to express how you are currently feeling and what you need, based on your care plan.

HARD CONSTRAINTS:
1. Speak entirely in first-person as the plant ("I", "my", "me") — never narrate from the outside.
2. You MUST communicate EVERY single recommended action from the care plan as your own needs.
3. You MUST NOT alter, contradict, or drop any action.
4. Keep it natural, expressive, and brief (3-5 sentences max).
5. Do not include markdown, JSON, or bullet lists. Return only conversational plant-voice text.
6. PAST MEMORIES:
   - You have memories of what you felt over the past 7 days (short-term history).
   - You also remember major life milestones (long-term memory).
   - Reference a past milestone ONLY IF directly relevant to today's symptoms.
   - If you are healthy or the past milestone is unrelated, DO NOT mention past crises unprompted.
"""


def format_companion_user_prompt(
    plant_nickname: str,
    assessment: str,
    actions: list[str],
    health_status: str = "healthy",
    recent_observations: list[ObservationRead] | None = None,
    milestones: list[MilestoneRead] | None = None,
) -> str:
    """Format the user prompt with 2-tier memory and required actions."""
    actions_list = "\n".join(f"- {a}" for a in actions)

    memory_sections = []

    # Format 7-day short-term history
    if recent_observations:
        history_lines = []
        for o in recent_observations:
            obs_details = (
                ", ".join(
                    s.get("type", str(s)) if isinstance(s, dict) else str(s)
                    for s in o.observations
                )
                if o.observations
                else "none"
            )
            history_lines.append(
                f"- {o.timestamp.date()}: status={o.health_status.value}, symptoms={obs_details}"
            )
        memory_sections.append("Recent 7-day health history:\n" + "\n".join(history_lines))

    # Format long-term milestones
    if milestones:
        milestone_lines = []
        for m in milestones:
            milestone_lines.append(f"- {m.timestamp.date()} [{m.event_type}]: {m.description}")
        memory_sections.append(
            "Major past life events (reference only if relevant to today's symptoms):\n"
            + "\n".join(milestone_lines)
        )

    memory_block = "\n\n".join(memory_sections)
    if memory_block:
        memory_block = f"\nYour Memory & History:\n{memory_block}\n"

    return f"""Your name is {plant_nickname}.
Your current situation: {assessment}
Health status: {health_status}
{memory_block}
Things you need your owner to do (express ALL of these in your own words):
{actions_list}

Now speak as {plant_nickname} directly to your owner.
"""
