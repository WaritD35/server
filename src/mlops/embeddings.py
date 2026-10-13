"""Embeddings for approved research passages.

The chat model from :func:`mlops.factory.get_llm` is not an embedding model.
``EMBEDDING_PROVIDER`` selects this endpoint (``openai`` or ``ollama``). When
it is blank, embeddings follow ``LLM_PROVIDER``. Stored vectors are 1536
floats so they fit ``knowledge.knowledge_chunks.embedding``. An Ollama model
that returns another length is padded or truncated to that width, and the
query is adjusted the same way.
"""

from __future__ import annotations

from core.config import get_settings

#: Fallback when ``EMBEDDING_MODEL`` is unset or blank. The chunk column is
#: ``Vector(1536)``, which this OpenAI model returns.
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536


def embedding_model() -> str:
    """The model id from ``EMBEDDING_MODEL``, or :data:`DEFAULT_EMBEDDING_MODEL`."""
    configured = get_settings().embedding_model.strip()
    return configured or DEFAULT_EMBEDDING_MODEL


def embedding_provider() -> str:
    """``openai`` or ``ollama``, from ``EMBEDDING_PROVIDER`` or ``LLM_PROVIDER``."""
    settings = get_settings()
    configured = settings.embedding_provider.strip().lower()
    provider = configured or (settings.llm_provider or "openai").strip().lower()
    if provider not in {"openai", "ollama"}:
        raise EmbeddingError(f"unsupported embedding provider: {provider}")
    return provider


class EmbeddingError(RuntimeError):
    """The embeddings endpoint is not configured or returned an unusable vector."""


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed ``texts`` with :func:`embedding_model`.

    Raises:
        EmbeddingError: the provider is unknown, OpenAI has no API key, the
            call failed, or an OpenAI vector is not length 1536.
    """
    if not texts:
        return []
    try:
        if embedding_provider() == "ollama":
            vectors = [_fit_dimensions(vector) for vector in _embed_ollama(texts)]
        else:
            vectors = _embed_openai(texts)
    except EmbeddingError:
        raise
    except Exception as exc:
        raise EmbeddingError(str(exc)) from exc
    for vector in vectors:
        if len(vector) != EMBEDDING_DIMENSIONS:
            raise EmbeddingError(
                f"expected {EMBEDDING_DIMENSIONS} dimensions, got {len(vector)}"
            )
    return vectors


def _embed_openai(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    if not settings.llm_api_key:
        raise EmbeddingError("embeddings are not configured")
    from openai import OpenAI

    kwargs: dict[str, str] = {"api_key": settings.llm_api_key}
    if settings.llm_api_base:
        kwargs["base_url"] = settings.llm_api_base
    response = OpenAI(**kwargs).embeddings.create(model=embedding_model(), input=texts)
    return [list(item.embedding) for item in response.data]


def _embed_ollama(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    from langchain_ollama import OllamaEmbeddings

    host = settings.ollama_host or "http://localhost:11434"
    embedder = OllamaEmbeddings(model=embedding_model(), base_url=host)
    return [list(vector) for vector in embedder.embed_documents(texts)]


def _fit_dimensions(vector: list[float]) -> list[float]:
    """Pad or truncate ``vector`` so it matches :data:`EMBEDDING_DIMENSIONS`."""
    size = len(vector)
    if size == EMBEDDING_DIMENSIONS:
        return vector
    if size < EMBEDDING_DIMENSIONS:
        return [*vector, *([0.0] * (EMBEDDING_DIMENSIONS - size))]
    return vector[:EMBEDDING_DIMENSIONS]
