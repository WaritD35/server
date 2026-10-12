"""Config parsing and the schedule arithmetic built on it."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from orchestrator_fakes import JST_0705, RAW_CONFIG

from orchestrator.config import ConfigError, OrchestratorConfig, load_config, parse_config
from orchestrator.service import due_slots, next_slot

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_committed_config_file_loads() -> None:
    config = load_config(REPO_ROOT / "config" / "orchestrator.toml")
    assert config.enabled
    assert config.schedule


def test_bad_config_is_rejected() -> None:
    with pytest.raises(ConfigError):
        parse_config({k: v for k, v in RAW_CONFIG.items() if k != "timezone"})
    with pytest.raises(ValueError):
        parse_config({**RAW_CONFIG, "schedule": ["25:00"]})
    with pytest.raises(ConfigError, match="stale_after_minutes"):
        parse_config({**RAW_CONFIG, "stale_after_minutes": 0})


def test_stale_after_defaults_to_an_hour() -> None:
    assert parse_config(RAW_CONFIG).stale_after == timedelta(minutes=60)
    assert parse_config({**RAW_CONFIG, "stale_after_minutes": 90}).stale_after == timedelta(
        minutes=90
    )


def test_due_slots_within_catch_up(config: OrchestratorConfig) -> None:
    slot_0700 = datetime(2026, 9, 26, 22, 0, tzinfo=UTC)
    assert due_slots(config, JST_0705) == [slot_0700]
    # before the slot, and once the catch-up window has passed
    assert due_slots(config, slot_0700 - timedelta(seconds=1)) == []
    assert due_slots(config, slot_0700 + timedelta(minutes=60)) == []


def test_slot_before_midnight_is_caught_after_it() -> None:
    config = parse_config({**RAW_CONFIG, "schedule": ["23:50"]})
    just_after_midnight_jst = datetime(2026, 9, 27, 15, 5, tzinfo=UTC)  # 00:05 on the 28th
    assert due_slots(config, just_after_midnight_jst) == [
        datetime(2026, 9, 27, 14, 50, tzinfo=UTC)
    ]


def test_next_slot(config: OrchestratorConfig) -> None:
    assert next_slot(config, JST_0705) == datetime(2026, 9, 27, 3, 0, tzinfo=UTC)  # 12:00 JST
    after_last = datetime(2026, 9, 27, 4, 0, tzinfo=UTC)  # 13:00 JST
    assert next_slot(config, after_last) == datetime(2026, 9, 27, 22, 0, tzinfo=UTC)
