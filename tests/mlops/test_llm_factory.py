"""Tests for the multi-provider LLM factory (Ollama & OpenAI)."""

from unittest.mock import patch

from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from core.config import Settings
from mlops.factory import get_llm
from mlops.settings import Component


def test_get_llm_openai_default():
    with patch("mlops.factory.get_settings") as mock_settings:
        mock_settings.return_value = Settings(
            app_env="local",
            log_level="INFO",
            database_url="sqlite://",
            minio_endpoint="localhost:9000",
            minio_access_key="minio",
            minio_secret_key="minio",
            minio_secure=False,
            minio_bucket_captures="captures",
            minio_bucket_frames_derived="frames",
            minio_bucket_exemplars="exemplars",
            minio_bucket_eval_sets="evals",
            jwt_issuer="pet-plant",
            jwt_audience="pet-plant-api",
            jwt_alg="HS256",
            jwt_secret="secret",
            access_token_ttl_seconds=3600,
            admin_email="admin@example.com",
            admin_name="Admin",
            admin_password="password",
            llm_provider="openai",
            llm_model="gpt-4o-mini",
        )
        model = get_llm(Component.ADVICE, temperature=0.3)
        assert isinstance(model, ChatOpenAI)
        assert model.model_name == "gpt-4o-mini"
        assert model.temperature == 0.3


def test_get_llm_ollama_provider():
    with patch("mlops.factory.get_settings") as mock_settings:
        mock_settings.return_value = Settings(
            app_env="local",
            log_level="INFO",
            database_url="sqlite://",
            minio_endpoint="localhost:9000",
            minio_access_key="minio",
            minio_secret_key="minio",
            minio_secure=False,
            minio_bucket_captures="captures",
            minio_bucket_frames_derived="frames",
            minio_bucket_exemplars="exemplars",
            minio_bucket_eval_sets="evals",
            jwt_issuer="pet-plant",
            jwt_audience="pet-plant-api",
            jwt_alg="HS256",
            jwt_secret="secret",
            access_token_ttl_seconds=3600,
            admin_email="admin@example.com",
            admin_name="Admin",
            admin_password="password",
            llm_provider="ollama",
            ollama_host="http://localhost:11434",
            ollama_model="llama3.2:latest",
        )
        model = get_llm(Component.COMPANION, temperature=0.7)
        assert isinstance(model, ChatOllama)
        assert model.model == "llama3.2:latest"
        assert model.base_url == "http://localhost:11434"
        assert model.temperature == 0.7
