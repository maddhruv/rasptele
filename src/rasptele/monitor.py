"""Host and Docker observations plus stateful alert transitions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from html import escape
from pathlib import Path

import httpx
import psutil

from .config import Config
from .store import Store


@dataclass(frozen=True)
class HostStats:
    cpu_percent: float
    memory_percent: float
    temperature_celsius: float | None
    throttled: bool | None
    disk_percent: float


def _read_first(paths: list[str]) -> str | None:
    for value in paths:
        try:
            return Path(value).read_text().strip()
        except OSError:
            continue
    return None


def host_stats() -> HostStats:
    """Read host metrics from the explicitly mounted read-only paths."""
    original_proc = psutil.PROCFS_PATH
    try:
        if Path("/host/proc").is_dir():
            psutil.PROCFS_PATH = "/host/proc"
        cpu = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory().percent
    finally:
        psutil.PROCFS_PATH = original_proc
    disk_root = "/host" if Path("/host").is_dir() else "/"
    disk = psutil.disk_usage(disk_root).percent
    raw_temp = _read_first(["/host/sys/class/thermal/thermal_zone0/temp", "/sys/class/thermal/thermal_zone0/temp"])
    temperature = None
    if raw_temp:
        try:
            temperature = int(raw_temp) / 1000
        except ValueError:
            pass
    raw_throttle = _read_first(
        [
            "/host/sys/devices/platform/soc/soc:firmware/get_throttled",
            "/sys/devices/platform/soc/soc:firmware/get_throttled",
        ]
    )
    throttled = None
    if raw_throttle:
        try:
            throttled = int(raw_throttle, 0) != 0
        except ValueError:
            pass
    return HostStats(cpu, memory, temperature, throttled, disk)


class Monitor:
    def __init__(self, config: Config, store: Store) -> None:
        self.config = config
        self.store = store
        self.client = httpx.AsyncClient(base_url=config.docker_guard_url, timeout=10)
        self._delivery_lock = asyncio.Lock()

    async def close(self) -> None:
        await self.client.aclose()

    async def containers(self) -> list[dict[str, object]]:
        response = await self.client.get("/v1/containers")
        response.raise_for_status()
        return response.json()

    async def restart(self, name: str) -> None:
        response = await self.client.post("/v1/restart", json={"name": name})
        response.raise_for_status()

    def _reconcile(self, key: str, active: bool, detail: str) -> None:
        messages = {
            transition: self._format(transition, detail)
            for transition in ("opened", "reminder", "recovered")
        }
        self.store.reconcile_incident(
            key,
            active,
            detail,
            self.config.reminder_interval_minutes * 60,
            messages,
        )

    async def check(self) -> None:
        stats = host_stats()
        checks = [
            ("disk", stats.disk_percent >= self.config.alerts.disk_percent, f"Disk is {stats.disk_percent:.1f}% full"),
            (
                "temperature",
                stats.temperature_celsius is not None
                and stats.temperature_celsius >= self.config.alerts.temperature_celsius,
                f"CPU temperature is {stats.temperature_celsius:.1f}°C" if stats.temperature_celsius is not None else "",
            ),
            ("throttling", stats.throttled is True, "Raspberry Pi reports throttling or under-voltage"),
        ]
        for key, active, detail in checks:
            self._reconcile(key, bool(active), detail or key)
        try:
            containers = await self.containers()
            if not isinstance(containers, list):
                raise ValueError("Docker guard returned a non-list response")
            if not all(
                isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and isinstance(item.get("name"), str)
                for item in containers
            ):
                raise ValueError("Docker guard returned malformed container data")
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            self._reconcile("docker_guard", True, "Docker guard is unavailable")
            return
        self._reconcile("docker_guard", False, "Docker monitoring restored")
        present: set[str] = set()
        for container in containers:
            name = str(container["name"])
            key = f"container:{name}"
            present.add(key)
            status = str(container.get("status") or "unknown")
            health = container.get("health")
            active = status != "running" or health == "unhealthy"
            detail = f"Container {name} is {status}" + (f" ({health})" if health else "")
            self._reconcile(key, active, detail)
        for key in self.store.active_incident_keys("container:") - present:
            name = key.split(":", 1)[1]
            self._reconcile(key, False, f"Container {name} was removed")

    async def _deliver_pending(self, notify) -> None:  # type: ignore[no-untyped-def]
        async with self._delivery_lock:
            for row in self.store.pending_notifications():
                try:
                    await notify(str(row["message"]))
                except Exception as exc:
                    self.store.audit(
                        "notification_delivery_failed",
                        f"id={row['id']} error={type(exc).__name__}",
                    )
                    continue
                self.store.acknowledge_notification(int(row["id"]))

    @staticmethod
    def _format(transition: str, detail: str) -> str:
        prefix = {"opened": "⚠️ Alert", "reminder": "🔁 Alert persists", "recovered": "✅ Recovered"}[transition]
        return f"{prefix}: {escape(detail)}"

    async def run(self, notify) -> None:  # type: ignore[no-untyped-def]
        events = asyncio.create_task(self._watch_events(notify))
        try:
            while True:
                try:
                    await self.check()
                    await self._deliver_pending(notify)
                    self.store.prune(self.config.audit_retention_days)
                except Exception as exc:  # The bot must keep monitoring after transient failures.
                    self.store.audit("monitor_error", type(exc).__name__)
                await asyncio.sleep(self.config.monitor_interval_seconds)
        finally:
            events.cancel()

    async def _watch_events(self, notify) -> None:  # type: ignore[no-untyped-def]
        """Reconcile promptly after sanitized Docker lifecycle events."""
        while True:
            try:
                async with self.client.stream("GET", "/v1/events", timeout=None) as response:
                    response.raise_for_status()
                    async for _ in response.aiter_lines():
                        await self.check()
                        await self._deliver_pending(notify)
            except Exception as exc:
                self.store.audit("docker_event_stream_error", type(exc).__name__)
                await asyncio.sleep(5)
