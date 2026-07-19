import inspect
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from rasptele.bot import create_dispatcher
from rasptele.config import AlertConfig, Config, PiholeConfig
from rasptele.pihole import PiholeClient, PiholeError, PiholeStatus, PiholeStatusRefreshError
from rasptele.store import Store


def make_config(database_path: str, *, configured: bool = True) -> Config:
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
        pihole=PiholeConfig("http://pihole.test", "application-password")
        if configured
        else None,
    )


def status(blocking: str = "enabled", timer: float | None = None) -> PiholeStatus:
    return PiholeStatus(
        blocking=blocking,
        timer_seconds=timer,
        queries_total=1234,
        queries_blocked=234,
        percent_blocked=19.0,
        domains_being_blocked=150000,
        active_clients=7,
    )


def keyboard_texts(call: object) -> list[str]:
    markup = call.kwargs["reply_markup"]  # type: ignore[attr-defined]
    return [button.text for row in markup.inline_keyboard for button in row]


def keyboard_callbacks(call: object) -> list[str]:
    markup = call.kwargs["reply_markup"]  # type: ignore[attr-defined]
    return [button.callback_data or "" for row in markup.inline_keyboard for button in row]


class PiholeBotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.directory.name) / "state.db")
        self.store = Store(self.database_path)
        self.config = make_config(self.database_path)
        self.monitor = MagicMock()
        self.pihole = MagicMock(spec=PiholeClient)
        self.bot = Bot(self.config.token)
        self.update_id = 0

    async def asyncTearDown(self) -> None:
        await self.bot.session.close()
        self.store.close()
        self.directory.cleanup()

    def dispatcher(self, *, configured: bool = True):
        config = self.config if configured else make_config(self.database_path, configured=False)
        parameters = inspect.signature(create_dispatcher).parameters
        if "pihole" in parameters:
            return create_dispatcher(
                config, self.store, self.monitor, self.pihole if configured else None
            )
        # Compatibility keeps the test module runnable while the production handler is RED.
        return create_dispatcher(config, self.store, self.monitor)

    def message(
        self,
        text: str,
        *,
        user_id: int = 42,
        chat_id: int = 42,
        chat_type: str = "private",
    ) -> Message:
        return Message(
            message_id=self.update_id + 1,
            date=datetime.now(UTC),
            chat=Chat(id=chat_id, type=chat_type),
            from_user=User(id=user_id, is_bot=False, first_name="Owner"),
            text=text,
        )

    def callback(
        self,
        data: str,
        *,
        user_id: int = 42,
        chat_id: int = 42,
        chat_type: str = "private",
    ) -> CallbackQuery:
        return CallbackQuery(
            id=f"callback-{self.update_id + 1}",
            from_user=User(id=user_id, is_bot=False, first_name="Owner"),
            chat_instance="test-chat",
            message=self.message(
                "Pi-hole status", user_id=user_id, chat_id=chat_id, chat_type=chat_type
            ),
            data=data,
        )

    async def feed(self, dispatcher, event: Message | CallbackQuery):
        self.update_id += 1
        update = (
            Update(update_id=self.update_id, message=event)
            if isinstance(event, Message)
            else Update(update_id=self.update_id, callback_query=event)
        )
        with (
            patch.object(Message, "answer", new_callable=AsyncMock) as message_answer,
            patch.object(Message, "edit_text", new_callable=AsyncMock) as edit_text,
            patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as callback_answer,
        ):
            await dispatcher.feed_update(self.bot, update)
        return message_answer, edit_text, callback_answer

    def audit_events(self) -> list[tuple[str, str]]:
        rows = self.store.connection.execute(
            "SELECT event_type, detail FROM audit_events ORDER BY id"
        ).fetchall()
        return [(str(row["event_type"]), str(row["detail"])) for row in rows]

    async def test_authorized_command_renders_live_enabled_metrics_and_disable_keyboard(self):
        self.pihole.status = AsyncMock(return_value=status())

        answer, _, _ = await self.feed(self.dispatcher(), self.message("/pihole"))

        self.pihole.status.assert_awaited_once_with()
        answer.assert_awaited_once()
        text = answer.await_args.args[0]
        for expected in ("Pi-hole", "enabled", "1,234", "234", "19.0%", "150,000", "7"):
            self.assertIn(expected, text)
        self.assertIn("Disable for 5 minutes", keyboard_texts(answer.await_args))
        self.assertIn("pihole-disable-request", keyboard_callbacks(answer.await_args))
        self.assertIn("pihole-refresh", keyboard_callbacks(answer.await_args))

    async def test_disabled_status_renders_timer_and_enable_keyboard(self):
        self.pihole.status = AsyncMock(return_value=status("disabled", 125))

        answer, _, _ = await self.feed(self.dispatcher(), self.message("/pihole"))

        text = answer.await_args.args[0]
        self.assertIn("disabled", text)
        self.assertIn("125", text)
        self.assertIn("Enable now", keyboard_texts(answer.await_args))
        self.assertIn("pihole-enable", keyboard_callbacks(answer.await_args))
        self.assertNotIn("pihole-disable-request", keyboard_callbacks(answer.await_args))

    async def test_unknown_state_offers_refresh_but_no_mutation(self):
        self.pihole.status = AsyncMock(return_value=status("unknown"))

        answer, _, _ = await self.feed(self.dispatcher(), self.message("/pihole"))

        callbacks = keyboard_callbacks(answer.await_args)
        self.assertEqual(callbacks, ["pihole-refresh"])

    async def test_refresh_fetches_live_status_and_edits_same_message(self):
        self.pihole.status = AsyncMock(return_value=status())

        _, edit_text, callback_answer = await self.feed(
            self.dispatcher(), self.callback("pihole-refresh")
        )

        self.pihole.status.assert_awaited_once_with()
        edit_text.assert_awaited_once()
        self.assertIn("1,234", edit_text.await_args.args[0])
        callback_answer.assert_awaited_once()

    async def test_status_failure_is_safe_audited_and_keeps_refresh_action(self):
        self.pihole.status = AsyncMock(
            side_effect=PiholeError("must-not-leak http://pihole sid-or-password")
        )

        answer, _, _ = await self.feed(self.dispatcher(), self.message("/pihole"))

        answer.assert_awaited_once()
        self.assertEqual(answer.await_args.args[0], "Pi-hole is unavailable. Try again later.")
        self.assertEqual(keyboard_callbacks(answer.await_args), ["pihole-refresh"])
        self.assertIn(
            ("pihole_status_failed", "reason_type=PiholeError"), self.audit_events()
        )
        self.assertNotIn("must-not-leak", repr(self.audit_events()) + repr(answer.await_args))

    async def test_unauthorized_user_and_group_never_call_pihole(self):
        dispatcher = self.dispatcher()

        other_answer, _, _ = await self.feed(
            dispatcher, self.message("/pihole", user_id=7, chat_id=7)
        )
        group_answer, _, _ = await self.feed(
            dispatcher, self.message("/pihole", chat_id=-100, chat_type="group")
        )

        self.pihole.status.assert_not_awaited()
        self.pihole.disable.assert_not_awaited()
        self.pihole.enable.assert_not_awaited()
        other_answer.assert_not_awaited()
        group_answer.assert_not_awaited()
        self.assertEqual(
            [event for event, _ in self.audit_events()],
            ["unauthorized_message", "unauthorized_message"],
        )

    async def test_unauthorized_mutation_callback_never_calls_pihole(self):
        _, edit_text, callback_answer = await self.feed(
            self.dispatcher(), self.callback("pihole-enable", user_id=7, chat_id=7)
        )

        self.pihole.enable.assert_not_awaited()
        self.pihole.disable.assert_not_awaited()
        edit_text.assert_not_awaited()
        callback_answer.assert_not_awaited()
        self.assertEqual(self.audit_events(), [("unauthorized_message", "telegram_user_id=7")])

    async def test_unconfigured_command_returns_explicit_message(self):
        answer, _, _ = await self.feed(
            self.dispatcher(configured=False), self.message("/pihole")
        )

        answer.assert_awaited_once_with("Pi-hole integration is not configured.")

    async def test_disable_request_creates_server_side_confirmation(self):
        _, edit_text, callback_answer = await self.feed(
            self.dispatcher(), self.callback("pihole-disable-request")
        )

        row = self.store.connection.execute(
            "SELECT token, user_id, action, target FROM confirmations"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual((row["user_id"], row["action"], row["target"]), (42, "pihole_disable", "300"))
        edit_text.assert_awaited_once()
        callbacks = keyboard_callbacks(edit_text.await_args)
        confirmation_callback = f"pihole-disable-confirm:{row['token']}"
        self.assertIn(confirmation_callback, callbacks)
        self.assertLessEqual(len(confirmation_callback.encode()), 64)
        callback_answer.assert_awaited_once()
        self.pihole.disable.assert_not_awaited()

    async def test_valid_disable_confirmation_calls_once_audits_and_edits(self):
        token = self.store.create_confirmation(42, "pihole_disable", "300")
        self.pihole.disable = AsyncMock(return_value=status("disabled", 300))

        _, edit_text, _ = await self.feed(
            self.dispatcher(), self.callback(f"pihole-disable-confirm:{token}")
        )

        self.pihole.disable.assert_awaited_once_with(300)
        edit_text.assert_awaited_once()
        self.assertIn("disabled", edit_text.await_args.args[0])
        self.assertIn(("pihole_disabled", "seconds=300"), self.audit_events())

    async def test_expired_disable_confirmation_never_calls_api(self):
        token = self.store.create_confirmation(42, "pihole_disable", "300")
        self.store.connection.execute(
            "UPDATE confirmations SET expires_at = 0 WHERE token = ?", (token,)
        )
        self.store.connection.commit()

        _, edit_text, callback_answer = await self.feed(
            self.dispatcher(), self.callback(f"pihole-disable-confirm:{token}")
        )

        self.pihole.disable.assert_not_awaited()
        edit_text.assert_not_awaited()
        callback_answer.assert_awaited_once_with(
            "Confirmation expired or already used", show_alert=True
        )

    async def test_enable_is_immediate_audited_and_edits_fresh_status(self):
        self.pihole.enable = AsyncMock(return_value=status("enabled"))

        _, edit_text, _ = await self.feed(self.dispatcher(), self.callback("pihole-enable"))

        self.pihole.enable.assert_awaited_once_with()
        edit_text.assert_awaited_once()
        self.assertIn("enabled", edit_text.await_args.args[0])
        self.assertIn("pihole_enabled", [event for event, _ in self.audit_events()])

    async def test_ordinary_action_failure_is_safe_and_audited(self):
        self.pihole.enable = AsyncMock(
            side_effect=PiholeError("must-not-leak http://pihole sid-or-password")
        )

        _, edit_text, _ = await self.feed(self.dispatcher(), self.callback("pihole-enable"))

        self.pihole.enable.assert_awaited_once_with()
        edit_text.assert_awaited_once_with("Pi-hole is unavailable. Try again later.")
        events = self.audit_events()
        self.assertIn(("pihole_enable_failed", "reason_type=PiholeError"), events)
        self.assertNotIn("must-not-leak", repr(events) + repr(edit_text.await_args))

    async def test_post_mutation_refresh_failure_audits_success_and_failure(self):
        cases = (
            ("enable", "pihole-enable", "pihole_enabled", "pihole_enable_status_failed"),
            (
                "disable",
                "pihole-disable-confirm",
                "pihole_disabled",
                "pihole_disable_status_failed",
            ),
        )
        dispatcher = self.dispatcher()
        for action, callback_data, success_event, failure_event in cases:
            with self.subTest(action=action):
                if action == "disable":
                    token = self.store.create_confirmation(42, "pihole_disable", "300")
                    callback_data = f"{callback_data}:{token}"
                method = AsyncMock(
                    side_effect=PiholeStatusRefreshError(action, "PiholeResponseError")
                )
                setattr(self.pihole, action, method)

                _, edit_text, _ = await self.feed(dispatcher, self.callback(callback_data))

                if action == "disable":
                    method.assert_awaited_once_with(300)
                else:
                    method.assert_awaited_once_with()
                edit_text.assert_awaited_once_with(
                    "Pi-hole was updated, but its current status could not be refreshed."
                )
                events = self.audit_events()
                self.assertIn(success_event, [event for event, _ in events])
                self.assertIn(
                    (failure_event, "reason_type=PiholeResponseError"), events
                )


if __name__ == "__main__":
    unittest.main()
