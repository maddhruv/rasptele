import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram import Bot
from aiogram.types import Chat, Message, Update, User

from rasptele.bot import _authorized, _container_keyboard, create_dispatcher
from rasptele.config import AlertConfig, Config
from rasptele.monitor import HostStats, Monitor
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
        restart_allowed=frozenset({"pihole"}),
        alerts=AlertConfig(),
    )


class BotSecurityTests(unittest.TestCase):
    def message(self, chat_type: str, chat_id: int, user_id: int = 42) -> Message:
        return Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=chat_id, type=chat_type),
            from_user=User(id=user_id, is_bot=False, first_name="Owner"),
            text="/status",
        )

    def test_only_owner_private_chat_is_authorized(self):
        config = make_config(":memory:")
        self.assertTrue(_authorized(self.message("private", 42), config))
        self.assertFalse(_authorized(self.message("group", -100), config))
        self.assertFalse(_authorized(self.message("private", 7, user_id=7), config))

    def test_callback_data_uses_short_container_id(self):
        keyboard = _container_keyboard(
            [{"id": "123456789abc", "name": "x" * 200, "status": "running"}]
        )
        callback_data = keyboard.inline_keyboard[0][0].callback_data
        self.assertIsNotNone(callback_data)
        self.assertLessEqual(len(callback_data.encode()), 64)


class DispatcherIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_dispatches_only_in_owner_private_chat(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(str(Path(directory) / "state.db"))
            config = make_config(store.connection.execute("PRAGMA database_list").fetchone()[2])
            monitor = MagicMock()
            monitor.containers = AsyncMock(return_value=[])
            dispatcher = create_dispatcher(config, store, monitor)
            bot = Bot(config.token)
            private = Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=42, type="private"),
                from_user=User(id=42, is_bot=False, first_name="Owner"),
                text="/status",
            )
            group = private.model_copy(
                update={"message_id": 2, "chat": Chat(id=-100, type="group")}
            )
            stats = HostStats(1, 2, 40, False, 10)
            try:
                with (
                    patch("rasptele.bot.host_stats", return_value=stats),
                    patch.object(Message, "answer", new_callable=AsyncMock) as answer,
                ):
                    await dispatcher.feed_update(bot, Update(update_id=1, message=private))
                    answer.assert_awaited_once()
                    answer.reset_mock()
                    await dispatcher.feed_update(bot, Update(update_id=2, message=group))
                    answer.assert_not_awaited()
                self.assertEqual(store.recent_audit()[0]["event_type"], "unauthorized_message")
            finally:
                await bot.session.close()
                store.close()


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        path = str(Path(self.directory.name) / "state.db")
        self.store = Store(path)
        self.monitor = Monitor(make_config(path), self.store)

    async def asyncTearDown(self):
        await self.monitor.close()
        self.store.close()
        self.directory.cleanup()

    async def test_removed_container_recovers_active_incident(self):
        self.store.raise_or_remind("container:gone", "Container gone is exited", 3600)
        self.monitor.containers = AsyncMock(return_value=[])
        healthy = HostStats(1, 2, 40, False, 10)
        with patch("rasptele.monitor.host_stats", return_value=healthy):
            await self.monitor.check()
        messages = [row["message"] for row in self.store.pending_notifications()]
        self.assertIn("✅ Recovered: Container gone was removed", messages)
        self.assertNotIn("container:gone", self.store.active_incident_keys("container:"))

    async def test_failed_delivery_does_not_block_later_notifications(self):
        self.monitor.containers = AsyncMock(return_value=[])
        full = HostStats(1, 2, 40, False, 95)
        with patch("rasptele.monitor.host_stats", return_value=full):
            await self.monitor.check()
        self.store.enqueue_notification("temperature", "Temperature high")
        notify = AsyncMock(side_effect=[RuntimeError("rejected"), None])
        await self.monitor._deliver_pending(notify)
        self.assertEqual(notify.await_count, 2)
        self.assertEqual(len(self.store.pending_notifications()), 1)
        retry = AsyncMock()
        await self.monitor._deliver_pending(retry)
        retry.assert_awaited_once()
        self.assertEqual(self.store.pending_notifications(), [])
