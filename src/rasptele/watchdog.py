"""Independent notifier for failures that the main bot cannot report itself."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from html import escape

import httpx
from aiogram import Bot

from .config import Config


class Watchdog:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = httpx.AsyncClient(base_url=config.docker_guard_url, timeout=10)
        self.bot = Bot(config.token)
        self.active: dict[str, str] = {}
        self._incident_lock = asyncio.Lock()

    async def close(self) -> None:
        await self.client.aclose()
        await self.bot.session.close()

    async def _set_incident(self, key: str, detail: str | None) -> None:
        async with self._incident_lock:
            previous = self.active.get(key)
            if detail is not None and previous is None:
                await self.bot.send_message(
                    self.config.allowed_user_id,
                    f"⚠️ Watchdog alert: {escape(detail)}",
                    parse_mode="HTML",
                )
                self.active[key] = detail
            elif detail is None and previous is not None:
                await self.bot.send_message(
                    self.config.allowed_user_id,
                    f"✅ Watchdog recovered: {escape(previous)}",
                    parse_mode="HTML",
                )
                self.active.pop(key, None)

    async def check(self) -> None:
        try:
            response = await self.client.get("/v1/containers")
            response.raise_for_status()
            containers = response.json()
            if not isinstance(containers, list):
                raise ValueError("invalid guard response")
        except (httpx.HTTPError, ValueError, TypeError):
            await self._set_incident("docker_guard", "Docker guard is unavailable")
            return
        await self._set_incident("docker_guard", None)
        bot_containers = [item for item in containers if item.get("compose_service") == "rasptele"]
        running = any(item.get("status") == "running" for item in bot_containers)
        await self._set_incident(
            "rasptele", None if running else "Rasptele bot container is not running"
        )

    async def run(self) -> None:
        events = asyncio.create_task(self._watch_events())
        try:
            while True:
                try:
                    await self.check()
                except Exception:
                    # Notification failures are retried without changing incident state.
                    pass
                await asyncio.sleep(self.config.monitor_interval_seconds)
        finally:
            events.cancel()
            with suppress(asyncio.CancelledError):
                await events
            await self.close()

    async def _watch_events(self) -> None:
        while True:
            try:
                async with self.client.stream("GET", "/v1/events", timeout=None) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        await self._handle_event(json.loads(line))
            except Exception:
                await asyncio.sleep(5)

    async def _handle_event(self, event: dict[str, object]) -> None:
        if event.get("compose_service") != "rasptele":
            return
        action = event.get("action")
        if action in {"die", "kill", "oom", "stop"}:
            await self._set_incident("rasptele", f"Rasptele bot container reported {action}")
        elif action in {"start", "restart"}:
            await self._set_incident("rasptele", None)


async def run_watchdog(config: Config) -> None:
    await Watchdog(config).run()
