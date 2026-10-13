"""Runtime execution for ``companion`` — plant personality dialogue generation.

``src/companion`` calls :func:`generate_companion_message` when a run needs care
advice: resolve the ``production`` prompt, call the LLM, trace it into the
companion Langfuse project.

The caller already has a deterministic template for every failure, so this
returns ``None`` rather than raising when the LLM cannot answer. When the
``production`` prompt cannot be fetched it runs the in-repo bootstrap prompt
instead (:data:`mlops.companion.prompts.BOOTSTRAP_MESSAGES`); a pinned
``version`` (an experiment) never falls back.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from langchain_core.output_parsers import StrOutputParser
from langfuse import propagate_attributes

from mlops.client import get_callback_handler, get_client, is_configured
from mlops.companion import prompts
from mlops.settings import PRODUCTION_LABEL, Component

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from langfuse.model import ChatPromptClient

    from assessment.schemas import MilestoneRead, ObservationRead

logger = logging.getLogger(__name__)

COMPONENT: Final = Component.COMPANION


@dataclass(frozen=True)
class CompanionConfig:
    """Hyper-parameters and tones, read from the Langfuse prompt version's ``config``.

    ``model`` is optional for the same reason as in ``advice``: the deployment
    picks the provider (``LLM_PROVIDER``) through :func:`mlops.factory.get_llm`.
    A version without ``tones`` / ``default_tone`` uses the bootstrap ones.
    """

    model: str | None = None
    temperature: float = 0.7
    max_tokens: int = prompts.MAX_REPLY_TOKENS
    tones: dict[str, str] = field(default_factory=dict)
    default_tone: str = ""

    def tone_for(self, health_status: str) -> str:
        return self.tones.get(health_status, self.default_tone)

    @classmethod
    def from_prompt_config(
        cls, config: Any, *, overrides: dict[str, Any] | None = None
    ) -> CompanionConfig:
        raw: dict[str, Any] = dict(config) if isinstance(config, dict) else {}
        raw.update(overrides or {})
        unknown = set(raw) - {"model", "temperature", "max_tokens", "tones", "default_tone"}
        if unknown:
            logger.warning("ignoring unknown companion prompt config key(s): %s", sorted(unknown))
        tones = raw.get("tones")
        return cls(
            model=str(raw.get("model") or "").strip() or None,
            temperature=float(raw.get("temperature", 0.7)),
            max_tokens=_reply_tokens(raw.get("max_tokens")),
            tones=(
                {str(k): str(v) for k, v in tones.items()}
                if isinstance(tones, dict)
                else dict(prompts.BOOTSTRAP_CONFIG["tones"])
            ),
            default_tone=str(
                raw.get("default_tone") or prompts.BOOTSTRAP_CONFIG["default_tone"]
            ),
        )


def _resolve_prompt(label: str | None, version: int | None) -> ChatPromptClient | None:
    """The Langfuse version to run, or ``None`` to use the bootstrap prompt.

    Only the request path (no pinned ``version``) may fall back.
    """
    if version is not None:
        return prompts.get(prompts.SPEAK_PROMPT, label=label, version=version)
    if not is_configured(COMPONENT):
        logger.debug("companion Langfuse project not configured; using the bootstrap prompt")
        return None
    try:
        return prompts.get(prompts.SPEAK_PROMPT, label=label)
    except Exception:
        logger.warning(
            "cannot fetch Langfuse prompt %r; using the bootstrap prompt",
            prompts.SPEAK_PROMPT,
            exc_info=True,
        )
        return None


def generate_companion_message(
    plant_nickname: str,
    assessment: str,
    actions: list[str],
    health_status: str = "healthy",
    recent_observations: list[ObservationRead] | None = None,
    milestones: list[MilestoneRead] | None = None,
    *,
    plant_id: str | None = None,
    run_id: str | None = None,
    label: str | None = PRODUCTION_LABEL,
    version: int | None = None,
    overrides: dict[str, Any] | None = None,
) -> str | None:
    """Generate a first-person plant persona message via LLM.

    Returns the raw generated string or None if generation failed or is unconfigured.
    ``plant_id`` / ``run_id`` are for tracing only (Langfuse trace metadata).
    ``label`` / ``version`` / ``overrides`` let an experiment pin a prompt version
    and drive this exact function.
    """
    metadata = {k: v for k, v in (("plant_id", plant_id), ("run_id", run_id)) if v}
    prompt = _resolve_prompt(label, version)
    config = CompanionConfig.from_prompt_config(
        prompt.config if prompt is not None else prompts.BOOTSTRAP_CONFIG,
        overrides=overrides,
    )
    variables = {
        "plant_nickname": plant_nickname,
        "assessment": assessment,
        "health_status": health_status,
        "tone": config.tone_for(health_status),
        "memory": prompts.format_memory(recent_observations, milestones),
        "actions": prompts.format_actions(actions),
    }
    messages = (
        prompt.compile(**variables) if prompt is not None else prompts.compile_bootstrap(variables)
    )

    try:
        return limit_companion_text(
            _invoke(messages, config, prompt, plant_nickname, metadata).strip()
        )
    except Exception as e:
        logger.warning("Companion LLM generation failed or unavailable: %s", e)
        return None


def _invoke(
    messages: Any,
    config: CompanionConfig,
    prompt: ChatPromptClient | None,
    plant_nickname: str,
    metadata: dict[str, str],
) -> str:
    """One chat call, traced when the companion project is configured."""
    # Imported here so tests (and the mock pipeline fixtures) can patch it.
    from mlops.factory import get_llm

    model = get_llm(
        COMPONENT,
        temperature=config.temperature,
        model_override=config.model,
        max_tokens=config.max_tokens,
    )
    chain = model | StrOutputParser()
    # Tuples, not a ChatPromptTemplate: the compiled text may contain braces
    # (symptoms, care text) that a template would read as variables.
    chat = [(m["role"], m["content"]) for m in messages]

    if not is_configured(COMPONENT):
        return chain.invoke(chat)

    invoke_config: RunnableConfig = {
        "callbacks": [get_callback_handler(COMPONENT)],
        "run_name": "speak",
    }
    # One trace per message; `propagate_attributes(prompt=…)` ties the generation
    # to the prompt version, and its metadata makes the trace findable by run.
    with (
        get_client(COMPONENT).start_as_current_observation(
            name="companion.generate_companion_message",
            as_type="chain",
            input={**metadata, "plant_nickname": plant_nickname},
        ) as span,
        propagate_attributes(prompt=prompt, metadata=metadata or None),
    ):
        text = chain.invoke(chat, config=invoke_config)
        span.update(output=text)
        return text


_SENTENCE_END: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?])\s+")


def _reply_tokens(value: Any) -> int:
    """A positive generation cap, or the default when the config value is unusable."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return prompts.MAX_REPLY_TOKENS
    if parsed < 32:
        return prompts.MAX_REPLY_TOKENS
    return min(parsed, 400)


def limit_companion_text(text: str) -> str:
    """Keep at most four sentences, and under :data:`prompts.MAX_REPLY_CHARS`."""
    body = text.strip()
    if not body:
        return body
    sentences = [part for part in _SENTENCE_END.split(body) if part.strip()]
    kept = " ".join(sentences[: prompts.MAX_REPLY_SENTENCES]).strip()
    if len(kept) <= prompts.MAX_REPLY_CHARS:
        return kept
    cut = kept[: prompts.MAX_REPLY_CHARS]
    end = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
    if end >= 40:
        return cut[: end + 1].strip()
    space = cut.rfind(" ")
    if space >= 40:
        return cut[:space].rstrip(" ,;:") + "."
    return cut.rstrip() + "."
