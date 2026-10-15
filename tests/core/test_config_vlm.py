"""VLM and capture settings: committed defaults from ``.env.example``."""

import os

import pytest

from core.config import Settings, get_settings


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for name in list(os.environ):
        if name.upper().startswith(("VLM_", "CAPTURE_")):
            monkeypatch.delenv(name)
    get_settings.cache_clear()
    return Settings()  # type: ignore[call-arg]


def test_no_vlm_model_setting(settings: Settings) -> None:
    assert not hasattr(settings, "vlm_model")
    assert "vlm_model" not in Settings.model_fields
