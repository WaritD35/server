"""Typed application configuration.

Field *values* are not hard-coded here — only their names and types. They are
read (each layer overriding the previous) from:

1. ``.env.example`` — committed defaults, the single source of default config;
2. ``.env`` — optional local overrides (git-ignored);
3. real environment variables — deployment / compose overrides.

Every context imports :func:`get_settings` rather than reading ``os.environ``.
"""

from functools import lru_cache

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env.example", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- app ---
    app_env: str
    log_level: str
    cors_origins: str = "*"

    # --- PostgreSQL ---
    database_url: str

    # --- MinIO / S3 (object storage; client lives in core.storage) ---
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_secure: bool
    minio_bucket_captures: str
    minio_bucket_frames_derived: str
    minio_bucket_exemplars: str
    minio_bucket_eval_sets: str

    # --- auth (OAuth2 / JWT, role-based) ---
    jwt_issuer: str
    jwt_audience: str
    jwt_alg: str
    jwt_secret: str
    access_token_ttl_seconds: int

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, v: str, info: ValidationInfo) -> str:
        app_env = str(info.data.get("app_env", "local")).lower()
        if app_env in ("staging", "prod", "production"):
            if v == "change-me" or len(v.encode("utf-8")) < 32:
                raise ValueError(
                    "JWT_SECRET must be at least 32 bytes and cannot be 'change-me' in staging/prod"
                )
        return v

    # --- auth: bootstrap admin (seeded on startup by core.users.ensure_admin_user) ---
    admin_email: str
    admin_name: str
    admin_password: str

    # --- orchestrator (pipeline schedule + failure policy, a TOML file) ---
    orchestrator_config: str = "config/orchestrator.toml"

    # --- external LLM (text only — never images; used by `mlops`) ---
    # Where to reach it and how to authenticate — deployment concerns. Which
    # model to run is not one of them: that belongs to the Langfuse prompt
    # version's `config`, so it is versioned with the text it was tuned against.
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:latest"
    llm_api_base: str = ""
    llm_api_key: str = ""

    # --- Langfuse (prompt management, tracing, datasets; used by `mlops`) ---
    # Langfuse is split by project, one per LLM-using component, so there is a
    # key pair per component rather than one for the server. Resolved by
    # `mlops.settings.credentials_for`.
    langfuse_host: str = ""
    langfuse_assessment_public_key: str = ""
    langfuse_assessment_secret_key: str = ""
    langfuse_advice_public_key: str = ""
    langfuse_advice_secret_key: str = ""
    langfuse_knowledge_public_key: str = ""
    langfuse_knowledge_secret_key: str = ""
    langfuse_registry_public_key: str = ""
    langfuse_registry_secret_key: str = ""
    langfuse_companion_public_key: str = ""
    langfuse_companion_secret_key: str = ""
    confidence_threshold: float = 0.5


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    # Values are supplied by the env-file / environment sources, not as
    # constructor arguments — hence the ignore for the "missing argument" check.
    return Settings()  # type: ignore[call-arg]
