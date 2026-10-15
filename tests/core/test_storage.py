from minio import Minio

from core.config import get_settings
from core.storage import bucket_names, get_minio_client, get_object_storage


def _reset_caches() -> None:
    get_settings.cache_clear()
    get_minio_client.cache_clear()


def test_client_is_a_cached_singleton() -> None:
    _reset_caches()
    client = get_minio_client()

    assert isinstance(client, Minio)
    assert get_minio_client() is client
    assert get_object_storage() is client
    _reset_caches()


def test_client_reads_endpoint_and_tls_from_settings(monkeypatch) -> None:
    monkeypatch.setenv("MINIO_ENDPOINT", "minio.internal:9000")
    monkeypatch.setenv("MINIO_SECURE", "true")
    _reset_caches()

    client = get_minio_client()

    assert client._base_url.host == "minio.internal:9000"
    assert client._base_url.is_https is True
    _reset_caches()


def test_bucket_names_come_from_settings(monkeypatch) -> None:
    monkeypatch.setenv("MINIO_BUCKET_CAPTURES", "captures-test")
    _reset_caches()

    names = bucket_names()

    assert names["captures"] == "captures-test"
    assert set(names) == {"captures", "frames_derived", "eval_sets"}
    _reset_caches()
