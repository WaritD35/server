"""LLM client factory supporting Ollama and OpenAI providers."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel

from core.config import get_settings
from mlops.settings import Component

logger = logging.getLogger(__name__)


def get_llm(
    component: Component | str | None = None,
    *,
    temperature: float = 0.2,
    model_override: str | None = None,
) -> BaseChatModel:
    """Return a configured BaseChatModel instance based on application settings.

    Supports:
      - 'ollama': Local self-hosted inference via ChatOllama.
      - 'openai': OpenAI-compatible API via ChatOpenAI.
    """
    settings = get_settings()
    provider = (settings.llm_provider or "openai").lower().strip()

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        selected_model = model_override or settings.ollama_model or "llama3.2:latest"
        base_url = settings.ollama_host or "http://localhost:11434"
        logger.debug("Initializing ChatOllama: base_url=%s, model=%s", base_url, selected_model)
        return ChatOllama(
            base_url=base_url,
            model=selected_model,
            temperature=temperature,
        )

    # Default: OpenAI
    from langchain_openai import ChatOpenAI

    selected_model = model_override or settings.llm_model or "gpt-4o-mini"
    kwargs: dict[str, Any] = {
        "model": selected_model,
        "temperature": temperature,
    }
    if settings.llm_api_base:
        kwargs["base_url"] = settings.llm_api_base
    if settings.llm_api_key:
        kwargs["api_key"] = settings.llm_api_key
    elif not kwargs.get("api_key"):
        # Allow offline initialization without failing on import/instantiation
        kwargs["api_key"] = "placeholder-key"

    logger.debug("Initializing ChatOpenAI: model=%s", selected_model)
    return ChatOpenAI(**kwargs)
