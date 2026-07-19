"""Configuration loading and fail-closed validation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml


class ConfigurationError(ValueError):
    """Raised when Rasptele cannot safely start."""


@dataclass(frozen=True)
class AlertConfig:
    disk_percent: float = 90.0
    temperature_celsius: float = 80.0


@dataclass(frozen=True)
class PiholeConfig:
    url: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class Config:
    token: str
    allowed_user_id: int
    database_path: str
    monitor_interval_seconds: int
    reminder_interval_minutes: int
    audit_retention_days: int
    docker_guard_url: str
    restart_allowed: frozenset[str]
    alerts: AlertConfig
    pihole: PiholeConfig | None = None


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise ConfigurationError(f"{name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if result <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return result


def _positive_float(value: Any, name: str, *, maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ConfigurationError(f"{name} must be a number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if not isfinite(result) or result <= 0 or (maximum is not None and result > maximum):
        suffix = f" and at most {maximum:g}" if maximum is not None else ""
        raise ConfigurationError(f"{name} must be greater than zero{suffix}")
    return result


def _http_url(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{name} must be an HTTP URL")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError(f"{name} must be an HTTP URL")
    return value.rstrip("/")


def load_config(
    path: str | Path,
    *,
    require_telegram: bool = True,
    require_integration_secrets: bool | None = None,
) -> Config:
    """Load YAML settings and, for the bot, required environment secrets."""
    if require_integration_secrets is None:
        require_integration_secrets = require_telegram
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    user_id = os.environ.get("TELEGRAM_ALLOWED_USER_ID", "").strip()
    if require_telegram and not token:
        raise ConfigurationError("TELEGRAM_BOT_TOKEN is required")
    if require_telegram and not user_id:
        raise ConfigurationError("TELEGRAM_ALLOWED_USER_ID is required")
    allowed_user_id = 0
    if require_telegram:
        try:
            allowed_user_id = int(user_id)
        except ValueError as exc:
            raise ConfigurationError("TELEGRAM_ALLOWED_USER_ID must be numeric") from exc
        if allowed_user_id <= 0:
            raise ConfigurationError("TELEGRAM_ALLOWED_USER_ID must be positive")

    config_path = Path(path)
    try:
        loaded = yaml.safe_load(config_path.read_text())
    except OSError as exc:
        raise ConfigurationError(f"cannot read config: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError("config is not valid YAML") from exc
    raw = {} if loaded is None else loaded
    if not isinstance(raw, dict):
        raise ConfigurationError("config root must be a mapping")

    alerts = raw.get("alerts", {})
    containers = raw.get("containers", {})
    integrations = raw.get("integrations", {})
    if not isinstance(alerts, dict) or not isinstance(containers, dict):
        raise ConfigurationError("alerts and containers must be mappings")
    if not isinstance(integrations, dict):
        raise ConfigurationError("integrations must be a mapping")
    names = containers.get("restart_allowed", [])
    if not isinstance(names, list) or not all(
        isinstance(item, str) and item and item.strip() == item for item in names
    ):
        raise ConfigurationError("containers.restart_allowed must be a list of names")

    disk = _positive_float(alerts.get("disk_percent", 90), "alerts.disk_percent", maximum=100)
    temp = _positive_float(alerts.get("temperature_celsius", 80), "alerts.temperature_celsius")
    database_path = raw.get("database_path", "/data/rasptele.sqlite3")
    docker_guard_url = _http_url(
        raw.get("docker_guard_url", "http://docker-guard:8080"), "docker_guard_url"
    )
    if not isinstance(database_path, str) or not database_path.strip():
        raise ConfigurationError("database_path must be a non-empty string")

    pihole: PiholeConfig | None = None
    if "pihole" in integrations:
        pihole_raw = integrations["pihole"]
        if not isinstance(pihole_raw, dict):
            raise ConfigurationError("integrations.pihole must be a mapping")
        pihole_url = _http_url(pihole_raw.get("url"), "integrations.pihole.url")
        pihole_password = os.environ.get("PIHOLE_PASSWORD", "").strip()
        if require_integration_secrets and not pihole_password:
            raise ConfigurationError("PIHOLE_PASSWORD is required when Pi-hole is configured")
        pihole = PiholeConfig(url=pihole_url, password=pihole_password)

    return Config(
        token=token,
        allowed_user_id=allowed_user_id,
        database_path=database_path.strip(),
        monitor_interval_seconds=_positive_int(raw.get("monitor_interval_seconds", 60), "monitor interval"),
        reminder_interval_minutes=_positive_int(
            raw.get("reminder_interval_minutes", 30), "reminder interval"
        ),
        audit_retention_days=_positive_int(raw.get("audit_retention_days", 90), "audit retention"),
        docker_guard_url=docker_guard_url,
        restart_allowed=frozenset(names),
        alerts=AlertConfig(disk_percent=disk, temperature_celsius=temp),
        pihole=pihole,
    )
