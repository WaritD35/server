"""Embedding provider selection: OpenAI and Ollama."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from mlops.embeddings import EMBEDDING_DIMENSIONS, EmbeddingError, embed_texts


def _settings(**overrides: str) -> SimpleNamespace:
    values = {
        "llm_provider": "openai",
        "embedding_provider": "",
        "embedding_model": "text-embedding-3-small",
        "llm_api_key": "sk-test",
        "llm_api_base": "https://api.openai.com/v1",
        "ollama_host": "http://localhost:11434",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_openai_requires_an_api_key() -> None:
    with patch("mlops.embeddings.get_settings", return_value=_settings(llm_api_key="")):
        with pytest.raises(EmbeddingError, match="not configured"):
            embed_texts(["leaf spot"])


def test_openai_uses_the_configured_model() -> None:
    item = SimpleNamespace(embedding=[0.1] * EMBEDDING_DIMENSIONS)
    response = SimpleNamespace(data=[item])
    client = MagicMock()
    client.embeddings.create.return_value = response
    with (
        patch("mlops.embeddings.get_settings", return_value=_settings()),
        patch("openai.OpenAI", return_value=client) as openai_cls,
    ):
        vectors = embed_texts(["leaf spot"])
    openai_cls.assert_called_once_with(
        api_key="sk-test", base_url="https://api.openai.com/v1"
    )
    client.embeddings.create.assert_called_once_with(
        model="text-embedding-3-small", input=["leaf spot"]
    )
    assert len(vectors[0]) == EMBEDDING_DIMENSIONS


def test_openai_rejects_the_wrong_width() -> None:
    item = SimpleNamespace(embedding=[0.1] * 3)
    response = SimpleNamespace(data=[item])
    client = MagicMock()
    client.embeddings.create.return_value = response
    with (
        patch("mlops.embeddings.get_settings", return_value=_settings()),
        patch("openai.OpenAI", return_value=client),
        pytest.raises(EmbeddingError, match="dimensions"),
    ):
        embed_texts(["leaf spot"])


def test_ollama_follows_llm_provider_and_fits_width() -> None:
    embedder = MagicMock()
    embedder.embed_documents.return_value = [[0.2] * 8]
    with (
        patch(
            "mlops.embeddings.get_settings",
            return_value=_settings(
                llm_provider="ollama",
                llm_api_key="",
                embedding_model="nomic-embed-text",
            ),
        ),
        patch("langchain_ollama.OllamaEmbeddings", return_value=embedder) as ollama_cls,
    ):
        vectors = embed_texts(["leaf spot"])
    ollama_cls.assert_called_once_with(
        model="nomic-embed-text", base_url="http://localhost:11434"
    )
    assert len(vectors[0]) == EMBEDDING_DIMENSIONS
    assert vectors[0][:8] == [0.2] * 8
    assert vectors[0][8:] == [0.0] * (EMBEDDING_DIMENSIONS - 8)


def test_embedding_provider_overrides_the_chat_provider() -> None:
    item = SimpleNamespace(embedding=[0.1] * EMBEDDING_DIMENSIONS)
    response = SimpleNamespace(data=[item])
    client = MagicMock()
    client.embeddings.create.return_value = response
    with (
        patch(
            "mlops.embeddings.get_settings",
            return_value=_settings(llm_provider="ollama", embedding_provider="openai"),
        ),
        patch("openai.OpenAI", return_value=client),
    ):
        embed_texts(["leaf spot"])
    client.embeddings.create.assert_called_once()
