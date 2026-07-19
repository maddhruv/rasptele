"""Configuration loading and fail-closed validation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from math import isfinite
from typing import Any
from urllib.parse import urlsplit


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


def _environment(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _restart_allowlist() -> frozenset[str]:
    names = _environment("RASPTELE_RESTART_ALLOWED").split(",")
    return frozenset(name.strip() for name in names if name.strip())


def load_config(*, require_telegram: bool = True, load_pihole: bool = True) -> Config:
    """Load and validate configuration from the process environment."""
    token = _environment("TELEGRAM_BOT_TOKEN")
    user_id = _environment("TELEGRAM_ALLOWED_USER_ID")
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

    pihole: PiholeConfig | None = None
    if load_pihole:
        pihole_url = _environment("PIHOLE_URL")
        pihole_password = _environment("PIHOLE_PASSWORD")
        if bool(pihole_url) != bool(pihole_password):
            raise ConfigurationError("PIHOLE_URL and PIHOLE_PASSWORD must be set together")
        if pihole_url:
            pihole = PiholeConfig(
                url=_http_url(pihole_url, "PIHOLE_URL"), password=pihole_password
            )

    return Config(
        token=token,
        allowed_user_id=allowed_user_id,
        database_path="/data/rasptele.sqlite3",
        monitor_interval_seconds=_positive_int(
            _environment("RASPTELE_MONITOR_INTERVAL_SECONDS", "60"),
            "RASPTELE_MONITOR_INTERVAL_SECONDS",
        ),
        reminder_interval_minutes=_positive_int(
            _environment("RASPTELE_REMINDER_INTERVAL_MINUTES", "30"),
            "RASPTELE_REMINDER_INTERVAL_MINUTES",
        ),
        audit_retention_days=_positive_int(
            _environment("RASPTELE_AUDIT_RETENTION_DAYS", "90"),
            "RASPTELE_AUDIT_RETENTION_DAYS",
        ),
        docker_guard_url="http://docker-guard:8080",
        restart_allowed=_restart_allowlist(),
        alerts=AlertConfig(
            disk_percent=_positive_float(
                _environment("RASPTELE_DISK_PERCENT", "90"),
                "RASPTELE_DISK_PERCENT",
                maximum=100,
            ),
            temperature_celsius=_positive_float(
                _environment("RASPTELE_TEMPERATURE_CELSIUS", "80"),
                "RASPTELE_TEMPERATURE_CELSIUS",
            ),
        ),
        pihole=pihole,
    )
