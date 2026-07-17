import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import HTTPException

from rasptele.config import AlertConfig, Config
from rasptele.guard import GuardState, create_app
from rasptele.watchdog import Watchdog


def make_config() -> Config:
    return Config(
        token="123:token",
        allowed_user_id=42,
        database_path=":memory:",
        monitor_interval_seconds=1,
        reminder_interval_minutes=30,
        audit_retention_days=90,
        docker_guard_url="http://guard",
        restart_allowed=frozenset({"allowed"}),
        alerts=AlertConfig(),
    )


class GuardTests(unittest.IsolatedAsyncioTestCase):
    def test_restart_allowlist_is_enforced_before_docker_lookup(self):
        state = GuardState.__new__(GuardState)
        state.config = make_config()
        state.client = MagicMock()
        with self.assertRaises(HTTPException) as raised:
            state.restart("denied")
        self.assertEqual(raised.exception.status_code, 403)
        state.client.containers.get.assert_not_called()

    def test_allowed_restart_reaches_docker(self):
        state = GuardState.__new__(GuardState)
        state.config = make_config()
        state.client = MagicMock()
        state.restart("allowed")
        state.client.containers.get.assert_called_once_with("allowed")
        state.client.containers.get.return_value.restart.assert_called_once_with()

    async def test_guard_http_routes_use_narrow_state_methods(self):
        state = MagicMock()
        state.containers.return_value = [{"id": "abc", "name": "allowed"}]
        with patch("rasptele.guard.GuardState", return_value=state):
            app = create_app(make_config())
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://guard") as client:
                self.assertEqual((await client.get("/healthz")).json(), {"ok": True})
                containers = (await client.get("/v1/containers")).json()
                self.assertEqual(containers[0]["name"], "allowed")
                response = await client.post("/v1/restart", json={"name": "allowed"})
        self.assertEqual(response.status_code, 200)
        state.restart.assert_called_once_with("allowed")


class WatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_notification_is_retried(self):
        watchdog = Watchdog.__new__(Watchdog)
        watchdog.config = make_config()
        watchdog.active = {}
        watchdog._incident_lock = asyncio.Lock()
        watchdog.bot = MagicMock()
        watchdog.bot.send_message = AsyncMock(side_effect=RuntimeError("offline"))
        with self.assertRaises(RuntimeError):
            await watchdog._set_incident("rasptele", "Rasptele is down")
        self.assertNotIn("rasptele", watchdog.active)
        watchdog.bot.send_message = AsyncMock()
        await watchdog._set_incident("rasptele", "Rasptele is down")
        self.assertIn("rasptele", watchdog.active)

    async def test_concurrent_events_send_one_alert_and_one_recovery(self):
        watchdog = Watchdog.__new__(Watchdog)
        watchdog.config = make_config()
        watchdog.active = {}
        watchdog._incident_lock = asyncio.Lock()
        watchdog.bot = MagicMock()
        watchdog.bot.send_message = AsyncMock()
        stopped = {"compose_service": "rasptele", "action": "die"}
        await asyncio.gather(watchdog._handle_event(stopped), watchdog._handle_event(stopped))
        self.assertEqual(watchdog.bot.send_message.await_count, 1)
        started = {"compose_service": "rasptele", "action": "start"}
        await asyncio.gather(watchdog._handle_event(started), watchdog._handle_event(started))
        self.assertEqual(watchdog.bot.send_message.await_count, 2)
