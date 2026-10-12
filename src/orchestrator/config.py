"""The pipeline schedule, read from a TOML file.

The file (``config/orchestrator.toml`` unless ``ORCHESTRATOR_CONFIG`` says
otherwise) holds *when* runs are queued. Deployment concerns (database URL, …)
stay in ``core.config``.
"""

import tomllib
from dataclasses import dataclass
from datetime import time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from core.config import get_settings


class ConfigError(ValueError):
    """The orchestrator config file is missing a value or has a bad one."""


@dataclass(frozen=True)
class OrchestratorConfig:
    enabled: bool
    poll_interval: timedelta
    timezone: ZoneInfo
    schedule: tuple[time, ...]
    catch_up: timedelta
    #: A run still ``running`` this long after it started is taken to belong to
    #: a process that stopped, and is marked ``failed``.
    stale_after: timedelta = timedelta(minutes=60)


def parse_config(raw: dict[str, Any]) -> OrchestratorConfig:
    """Validate a parsed TOML document. Split out so tests can pass a dict."""
    try:
        config = OrchestratorConfig(
            enabled=bool(raw.get("enabled", True)),
            poll_interval=timedelta(seconds=raw.get("poll_seconds", 30)),
            timezone=ZoneInfo(raw["timezone"]),
            schedule=tuple(sorted(time.fromisoformat(t) for t in raw["schedule"])),
            catch_up=timedelta(minutes=raw.get("catch_up_minutes", 60)),
            stale_after=timedelta(minutes=raw.get("stale_after_minutes", 60)),
        )
    except KeyError as exc:
        raise ConfigError(f"missing required key {exc}") from None
    if config.stale_after <= timedelta(0):
        raise ConfigError("stale_after_minutes must be positive")
    return config


def load_config(path: str | Path | None = None) -> OrchestratorConfig:
    path = Path(path or get_settings().orchestrator_config)
    with path.open("rb") as fh:
        return parse_config(tomllib.load(fh))
