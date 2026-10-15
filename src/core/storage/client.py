"""MinIO / S3 client factory and bucket helpers.

Pure mechanism: ``core`` provides the client and the configured bucket names;
each context decides what it stores and under which keys. Local bucket creation
is handled by the ``createbuckets`` service in ``compose.yaml``; :func:`ensure_bucket`
is available for contexts / tests that need it explicitly.
"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from minio import Minio

from core.config import Settings, get_settings


@lru_cache
def get_minio_client() -> Minio:
    """Return the process-wide MinIO client built from settings."""
    settings = get_settings()
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def get_object_storage() -> Minio:
    """FastAPI dependency yielding the shared MinIO client."""
    return get_minio_client()


ObjectStorage = Annotated[Minio, Depends(get_object_storage)]
"""Convenience alias for route signatures in other contexts' ``api.py``."""


def bucket_names(settings: Settings | None = None) -> dict[str, str]:
    """Return the configured bucket names keyed by their logical role."""
    settings = settings or get_settings()
    return {
        "captures": settings.minio_bucket_captures,
        "frames_derived": settings.minio_bucket_frames_derived,
        "eval_sets": settings.minio_bucket_eval_sets,
    }


def ensure_bucket(name: str, *, client: Minio | None = None) -> None:
    """Create ``name`` if it does not already exist (idempotent)."""
    client = client or get_minio_client()
    if not client.bucket_exists(name):
        client.make_bucket(name)
