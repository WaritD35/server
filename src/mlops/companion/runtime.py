"""Runtime execution for ``companion`` — plant personality dialogue generation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from mlops.client import get_callback_handler, is_configured
from mlops.companion.prompts import (
    format_companion_user_prompt,
    get_companion_system_prompt,
)
from mlops.settings import Component

if TYPE_CHECKING:
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.runnables import RunnableConfig

    from assessment.schemas import MilestoneRead, ObservationRead

logger = logging.getLogger(__name__)


def generate_companion_message(
    plant_nickname: str,
    assessment: str,
    actions: list[str],
    health_status: str = "healthy",
    recent_observations: list[ObservationRead] | None = None,
    milestones: list[MilestoneRead] | None = None,
) -> str | None:
    """Generate a first-person plant persona message via LLM.

    Returns the raw generated string or None if generation failed or is unconfigured.
    """
    callbacks: list[BaseCallbackHandler] = []
    if is_configured(Component.COMPANION):
        try:
            callbacks.append(get_callback_handler(Component.COMPANION))
        except Exception as e:
            logger.debug("Failed to initialize Langfuse callback for companion: %s", e)

    system_prompt = get_companion_system_prompt(health_status)
    user_prompt = format_companion_user_prompt(
        plant_nickname=plant_nickname,
        assessment=assessment,
        actions=actions,
        health_status=health_status,
        recent_observations=recent_observations,
        milestones=milestones,
    )

    try:
        from mlops.factory import get_llm

        model = get_llm(Component.COMPANION, temperature=0.7)
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("user", user_prompt),
        ])
        chain = prompt | model | StrOutputParser()
        cfg: RunnableConfig = {"callbacks": callbacks} if callbacks else {}
        output = chain.invoke({}, config=cfg)
        return output.strip()
    except Exception as e:
        logger.warning("Companion LLM generation failed or unavailable: %s", e)
        return None
