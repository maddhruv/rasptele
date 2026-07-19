import asyncio
import tempfile
import unittest
from contextlib import suppress
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from rasptele.bot import run_bot
from rasptele.config import AlertConfig, Config, PiholeConfig
from rasptele.monitor import HostStats, Monitor
from rasptele.pihole import (
    PiholeAuthenticationError,
    PiholeClient,
    PiholeError,
    PiholeStatus,
)
from rasptele.store import Store


def make_config(database_path: str) -> Config:
    return Config(
        token="123:token",
        allowed_user_id=42,
        database_path=database_path,
        monitor_interval_seconds=1,
        reminder_interval_minutes=30,
        audit_retention_days=90,
        docker_guard_url="http://guard",
        restart_allowed=frozenset(),
        alerts=AlertConfig(),
        pihole=PiholeConfig(url="http://pihole", password="application-password"),
    )


def healthy_status() -> PiholeStatus:
    return PiholeStatus(
        blocking="enabled",
        timer_seconds=None,
        queries_total=1000,
        queries_blocked=200,
        percent_blocked=20.0,
        domains_being_blocked=150000,
        active_clients=5,
    )


class PiholeMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.directory.name) / "state.db")
        self.store = Store(self.database_path)
        self.pihole = AsyncMock(spec=PiholeClient)
        self.pihole.status.return_value = healthy_status()
        self.monitor = Monitor(make_config(self.database_path), self.store, pihole=self.pihole)
        self.monitor.containers = AsyncMock(return_value=[])
        self.healthy_host = HostStats(1, 2, 40, False, 10)

    async def asyncTearDown(self) -> None:
        await self.monitor.close()
        self.store.close()
        self.directory.cleanup()

    async def check(self, stats: HostStats | None = None) -> None:
        with patch("rasptele.monitor.host_stats", return_value=stats or self.healthy_host):
            await self.monitor.check()

    def audit_types(self) -> list[str]:
        return [str(row["event_type"]) for row in self.store.recent_audit(100)]

    def pihole_notifications(self) -> list[object]:
        return [
            row
            for row in self.store.pending_notifications()
            if row["incident_key"] == "pihole"
        ]

    async def test_success_has_no_pihole_incident_or_notification(self) -> None:
        await self.check()

        self.pihole.status.assert_awaited_once_with()
        self.assertNotIn("pihole", self.store.active_incident_keys(""))
        self.assertEqual(self.pihole_notifications(), [])

    async def test_authentication_failure_opens_durable_alert_and_is_rate_limited(self) -> None:
        self.pihole.status.side_effect = PiholeAuthenticationError("rejected")

        await self.check()
        await self.check()

        self.assertIn("pihole", self.store.active_incident_keys(""))
        notifications = self.pihole_notifications()
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["message"], "⚠️ Alert: Pi-hole service is unavailable")
        self.assertEqual(self.audit_types().count("pihole_auth_failed"), 1)
        self.assertNotIn("pihole_check_failed", self.audit_types())

    async def test_general_failure_opens_durable_alert_and_is_rate_limited(self) -> None:
        self.pihole.status.side_effect = PiholeError("request failed")

        await self.check()
        await self.check()

        self.assertIn("pihole", self.store.active_incident_keys(""))
        self.assertEqual(len(self.pihole_notifications()), 1)
        self.assertEqual(self.audit_types().count("pihole_check_failed"), 1)
        self.assertNotIn("pihole_auth_failed", self.audit_types())

    async def test_general_failure_sends_and_audits_reminder_when_due(self) -> None:
        self.pihole.status.side_effect = PiholeError("request failed")

        await self.check()
        self.store.connection.execute(
            "UPDATE incidents SET last_notified_at = 0 WHERE key = 'pihole'"
        )
        self.store.connection.commit()
        await self.check()

        messages = [str(row["message"]) for row in self.pihole_notifications()]
        self.assertEqual(
            messages,
            [
                "⚠️ Alert: Pi-hole service is unavailable",
                "🔁 Alert persists: Pi-hole service is unavailable",
            ],
        )
        self.assertEqual(self.audit_types().count("pihole_check_failed"), 2)

    async def test_success_recovers_an_active_pihole_incident(self) -> None:
        self.pihole.status.side_effect = [PiholeError("offline"), healthy_status()]

        await self.check()
        await self.check()

        self.assertNotIn("pihole", self.store.active_incident_keys(""))
        messages = [str(row["message"]) for row in self.pihole_notifications()]
        self.assertEqual(
            messages,
            [
                "⚠️ Alert: Pi-hole service is unavailable",
                "✅ Recovered: Pi-hole service restored",
            ],
        )

    async def test_docker_guard_failure_still_checks_pihole(self) -> None:
        request = httpx.Request("GET", "http://guard/v1/containers")
        self.monitor.containers.side_effect = httpx.ConnectError("offline", request=request)

        await self.check()

        self.pihole.status.assert_awaited_once_with()
        self.assertIn("docker_guard", self.store.active_incident_keys(""))
        self.assertNotIn("pihole", self.store.active_incident_keys(""))

    async def test_unavailable_docker_inventory_preserves_active_container_incidents(self) -> None:
        self.store.raise_or_remind("container:missing", "Container missing is exited", 3600)
        request = httpx.Request("GET", "http://guard/v1/containers")
        self.monitor.containers.side_effect = httpx.ConnectError("offline", request=request)

        await self.check()

        self.assertIn("container:missing", self.store.active_incident_keys("container:"))
        self.pihole.status.assert_awaited_once_with()

    async def test_pihole_failure_does_not_suppress_host_or_docker_reconciliation(self) -> None:
        self.pihole.status.side_effect = PiholeError("offline")
        self.monitor.containers.return_value = [
            {"id": "abc123", "name": "broken", "status": "exited", "health": None}
        ]
        full_disk = HostStats(1, 2, 40, False, 95)

        await self.check(full_disk)

        active = self.store.active_incident_keys("")
        self.assertTrue({"disk", "container:broken", "pihole"}.issubset(active))
        incident_keys = {
            str(row["incident_key"]) for row in self.store.pending_notifications()
        }
        self.assertTrue({"disk", "container:broken", "pihole"}.issubset(incident_keys))


class MissingPiholeMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_none_client_performs_no_pihole_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = str(Path(directory) / "state.db")
            store = Store(database_path)
            monitor = Monitor(make_config(database_path), store, pihole=None)
            monitor.containers = AsyncMock(return_value=[])
            healthy_host = HostStats(1, 2, 40, False, 10)
            try:
                with (
                    patch("rasptele.monitor.host_stats", return_value=healthy_host),
                    patch.object(PiholeClient, "status", new_callable=AsyncMock) as status,
                ):
                    await monitor.check()
                status.assert_not_awaited()
                self.assertNotIn("pihole", store.active_incident_keys(""))
            finally:
                await monitor.close()
                store.close()


class PiholeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def make_dependencies(self):
        config = make_config(":memory:")
        store = MagicMock(spec=Store)
        monitor = MagicMock(spec=Monitor)
        monitor.run = AsyncMock()
        monitor.close = AsyncMock()
        pihole = MagicMock(spec=PiholeClient)
        pihole.close = AsyncMock()
        bot = MagicMock()
        bot.session.close = AsyncMock()
        dispatcher = MagicMock()
        dispatcher.start_polling = AsyncMock()
        dispatcher.resolve_used_update_types.return_value = []
        return config, store, monitor, pihole, bot, dispatcher

    async def test_run_bot_closes_shared_pihole_client_exactly_once(self) -> None:
        config, store, monitor, pihole, bot, dispatcher = self.make_dependencies()

        with (
            patch("rasptele.bot.Bot", return_value=bot),
            patch(
                "rasptele.bot.create_dispatcher", return_value=dispatcher
            ) as create_dispatcher,
        ):
            await run_bot(config, store, monitor, pihole)

        create_dispatcher.assert_called_once_with(config, store, monitor, pihole)
        monitor.close.assert_awaited_once_with()
        pihole.close.assert_awaited_once_with()
        bot.session.close.assert_awaited_once_with()

    async def test_monitor_waits_for_event_task_cancellation(self) -> None:
        config = make_config(":memory:")
        store = Store(":memory:")
        monitor = Monitor(config, store)
        monitor.check = AsyncMock()
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def watch_events(notify) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        monitor._watch_events = watch_events
        task = asyncio.create_task(monitor.run(AsyncMock()))
        await started.wait()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

        self.assertTrue(stopped.is_set())
        await monitor.close()
        store.close()

    async def test_run_bot_attempts_all_cleanup_when_monitor_close_fails(self) -> None:
        config, store, monitor, pihole, bot, dispatcher = self.make_dependencies()
        monitor.close.side_effect = RuntimeError("close failed")

        with (
            patch("rasptele.bot.Bot", return_value=bot),
            patch("rasptele.bot.create_dispatcher", return_value=dispatcher),
            self.assertRaisesRegex(RuntimeError, "close failed"),
        ):
            await run_bot(config, store, monitor, pihole)

        monitor.close.assert_awaited_once_with()
        pihole.close.assert_awaited_once_with()
        bot.session.close.assert_awaited_once_with()

    async def test_run_bot_cleans_resources_when_dispatcher_setup_fails(self) -> None:
        config, store, monitor, pihole, bot, _ = self.make_dependencies()

        with (
            patch("rasptele.bot.Bot", return_value=bot),
            patch(
                "rasptele.bot.create_dispatcher",
                side_effect=RuntimeError("dispatcher setup failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "dispatcher setup failed"),
        ):
            await run_bot(config, store, monitor, pihole)

        monitor.run.assert_not_called()
        monitor.close.assert_awaited_once_with()
        pihole.close.assert_awaited_once_with()
        bot.session.close.assert_awaited_once_with()
