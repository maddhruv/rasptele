"""Telegram interface. Every handler is guarded by the configured user ID."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .config import Config
from .monitor import Monitor, host_stats
from .pihole import PiholeClient, PiholeError, PiholeStatus, PiholeStatusRefreshError
from .store import Store


def _authorized(event: Message | CallbackQuery, config: Config) -> bool:
    user = event.from_user
    if user is None or user.id != config.allowed_user_id:
        return False
    if isinstance(event, Message):
        return event.chat.type == "private" and event.chat.id == config.allowed_user_id
    message = event.message
    return (
        isinstance(message, Message)
        and message.chat.type == "private"
        and message.chat.id == config.allowed_user_id
    )


def _container_keyboard(containers: list[dict[str, object]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{item['name']} · {item['status']}", callback_data=f"container:{item['id']}")]
        for item in containers
    ]
    rows.append([InlineKeyboardButton(text="Refresh", callback_data="containers")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _pihole_view(status: PiholeStatus) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "🕳 <b>Pi-hole</b>\n"
        f"Blocking: {escape(status.blocking)}\n"
        f"Queries: {status.queries_total:,}\n"
        f"Blocked: {status.queries_blocked:,} ({status.percent_blocked:.1f}%)\n"
        f"Blocklist domains: {status.domains_being_blocked:,}\n"
        f"Active clients: {status.active_clients:,}"
    )
    rows: list[list[InlineKeyboardButton]] = []
    if status.blocking == "enabled":
        rows.append(
            [
                InlineKeyboardButton(
                    text="Disable for 5 minutes", callback_data="pihole-disable-request"
                )
            ]
        )
    elif status.blocking == "disabled":
        if status.timer_seconds is not None:
            text += f"\nTimer: {status.timer_seconds:g} seconds"
        rows.append([InlineKeyboardButton(text="Enable now", callback_data="pihole-enable")])
    rows.append([InlineKeyboardButton(text="Refresh", callback_data="pihole-refresh")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _pihole_refresh_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Refresh", callback_data="pihole-refresh")]
        ]
    )


def create_dispatcher(
    config: Config,
    store: Store,
    monitor: Monitor,
    pihole: PiholeClient | None = None,
) -> Dispatcher:
    router = Router()

    async def deny_if_needed(event: Message | CallbackQuery) -> bool:
        if _authorized(event, config):
            return False
        if event.from_user:
            store.audit("unauthorized_message", f"telegram_user_id={event.from_user.id}")
        return True

    @router.message(Command("start", "help"))
    async def help_command(message: Message) -> None:
        if await deny_if_needed(message):
            return
        commands = "/status, /containers, /audit"
        if pihole is not None:
            commands += ", or /pihole"
        await message.answer(f"Rasptele is online. Use {commands}.")

    @router.message(Command("status"))
    async def status(message: Message) -> None:
        if await deny_if_needed(message):
            return
        stats = host_stats()
        try:
            containers = await monitor.containers()
            unhealthy = sum(
                item.get("status") != "running" or item.get("health") == "unhealthy"
                for item in containers
            )
            container_line = f"Containers: {len(containers)} total, {unhealthy} unhealthy"
        except Exception:
            container_line = "Containers: Docker guard unavailable"
        temp = "unavailable" if stats.temperature_celsius is None else f"{stats.temperature_celsius:.1f}°C"
        throttle = "unavailable" if stats.throttled is None else ("yes" if stats.throttled else "no")
        await message.answer(
            "📊 <b>Host status</b>\n"
            f"CPU: {stats.cpu_percent:.1f}%\nRAM: {stats.memory_percent:.1f}%\n"
            f"Temperature: {temp}\nThrottled: {throttle}\nDisk: {stats.disk_percent:.1f}%\n"
            f"{container_line}",
        )

    async def show_containers(message: Message | CallbackQuery) -> None:
        if await deny_if_needed(message):
            return
        try:
            containers = await monitor.containers()
        except Exception:
            text = "Docker guard is unavailable. Core monitoring will retry automatically."
            if isinstance(message, CallbackQuery):
                await message.answer(text, show_alert=True)
            else:
                await message.answer(text)
            return
        text = "🐳 <b>Containers</b>\nSelect one for details."
        keyboard = _container_keyboard(containers)
        if isinstance(message, CallbackQuery):
            assert isinstance(message.message, Message)
            await message.message.edit_text(text, reply_markup=keyboard)
            await message.answer()
        else:
            await message.answer(text, reply_markup=keyboard)

    @router.message(Command("containers"))
    async def containers_command(message: Message) -> None:
        await show_containers(message)

    async def show_pihole(event: Message | CallbackQuery) -> None:
        if await deny_if_needed(event):
            return
        if pihole is None:
            if isinstance(event, CallbackQuery):
                await event.answer("Pi-hole integration is not configured.", show_alert=True)
            else:
                await event.answer("Pi-hole integration is not configured.")
            return
        try:
            status = await pihole.status()
        except PiholeError as exc:
            store.audit("pihole_status_failed", f"reason_type={type(exc).__name__}")
            text = "Pi-hole is unavailable. Try again later."
            keyboard = _pihole_refresh_keyboard()
            if isinstance(event, CallbackQuery):
                assert isinstance(event.message, Message)
                await event.message.edit_text(text, reply_markup=keyboard)
                await event.answer()
            else:
                await event.answer(text, reply_markup=keyboard)
            return
        text, keyboard = _pihole_view(status)
        if isinstance(event, CallbackQuery):
            assert isinstance(event.message, Message)
            await event.message.edit_text(text, reply_markup=keyboard)
            await event.answer()
        else:
            await event.answer(text, reply_markup=keyboard)

    @router.message(Command("pihole"))
    async def pihole_command(message: Message) -> None:
        await show_pihole(message)

    @router.message(Command("audit"))
    async def audit(message: Message) -> None:
        if await deny_if_needed(message):
            return
        rows = store.recent_audit()
        if not rows:
            await message.answer("No audit events recorded yet.")
            return
        body = []
        for row in rows:
            stamp = datetime.fromtimestamp(row["occurred_at"]).strftime("%Y-%m-%d %H:%M")
            body.append(
                f"{stamp} · {escape(str(row['event_type']))} · {escape(str(row['detail']))}"
            )
        await message.answer("🧾 <b>Recent audit events</b>\n" + "\n".join(body))

    @router.callback_query(F.data == "containers")
    async def refresh_containers(query: CallbackQuery) -> None:
        await show_containers(query)

    @router.callback_query(F.data == "pihole-refresh")
    async def refresh_pihole(query: CallbackQuery) -> None:
        await show_pihole(query)

    @router.callback_query(F.data == "pihole-disable-request")
    async def pihole_disable_request(query: CallbackQuery) -> None:
        if await deny_if_needed(query):
            return
        if pihole is None:
            await query.answer("Pi-hole integration is not configured.", show_alert=True)
            return
        assert isinstance(query.message, Message)
        token = store.create_confirmation(config.allowed_user_id, "pihole_disable", "300")
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Confirm disable",
                        callback_data=f"pihole-disable-confirm:{token}",
                    )
                ],
                [InlineKeyboardButton(text="Cancel", callback_data="pihole-refresh")],
            ]
        )
        await query.message.edit_text(
            "⚠️ Disable Pi-hole blocking for 5 minutes? This action expires in 60 seconds.",
            reply_markup=keyboard,
        )
        await query.answer()

    @router.callback_query(F.data.startswith("pihole-disable-confirm:"))
    async def pihole_disable_confirm(query: CallbackQuery) -> None:
        if await deny_if_needed(query):
            return
        if pihole is None:
            await query.answer("Pi-hole integration is not configured.", show_alert=True)
            return
        assert isinstance(query.message, Message)
        token = (query.data or "").split(":", 1)[1]
        seconds = store.consume_confirmation_target(
            token, config.allowed_user_id, "pihole_disable"
        )
        if seconds != "300":
            await query.answer("Confirmation expired or already used", show_alert=True)
            return
        try:
            status = await pihole.disable(300)
        except PiholeStatusRefreshError as exc:
            store.audit("pihole_disabled", "seconds=300")
            store.audit(
                "pihole_disable_status_failed", f"reason_type={exc.reason_type}"
            )
            await query.message.edit_text(
                "Pi-hole was updated, but its current status could not be refreshed."
            )
        except PiholeError as exc:
            store.audit("pihole_disable_failed", f"reason_type={type(exc).__name__}")
            await query.message.edit_text("Pi-hole is unavailable. Try again later.")
        else:
            store.audit("pihole_disabled", "seconds=300")
            text, keyboard = _pihole_view(status)
            await query.message.edit_text(text, reply_markup=keyboard)
        await query.answer()

    @router.callback_query(F.data == "pihole-enable")
    async def pihole_enable(query: CallbackQuery) -> None:
        if await deny_if_needed(query):
            return
        if pihole is None:
            await query.answer("Pi-hole integration is not configured.", show_alert=True)
            return
        assert isinstance(query.message, Message)
        try:
            status = await pihole.enable()
        except PiholeStatusRefreshError as exc:
            store.audit("pihole_enabled", "")
            store.audit("pihole_enable_status_failed", f"reason_type={exc.reason_type}")
            await query.message.edit_text(
                "Pi-hole was updated, but its current status could not be refreshed."
            )
        except PiholeError as exc:
            store.audit("pihole_enable_failed", f"reason_type={type(exc).__name__}")
            await query.message.edit_text("Pi-hole is unavailable. Try again later.")
        else:
            store.audit("pihole_enabled", "")
            text, keyboard = _pihole_view(status)
            await query.message.edit_text(text, reply_markup=keyboard)
        await query.answer()

    @router.callback_query(F.data.startswith("container:"))
    async def container_detail(query: CallbackQuery) -> None:
        if await deny_if_needed(query):
            return
        assert isinstance(query.message, Message)
        container_id = (query.data or "").split(":", 1)[1]
        try:
            containers = await monitor.containers()
        except Exception:
            await query.answer("Docker guard is unavailable", show_alert=True)
            return
        item = next((value for value in containers if value["id"] == container_id), None)
        if item is None:
            await query.answer("Container no longer exists", show_alert=True)
            return
        name = str(item["name"])
        text = (
            f"<b>{escape(name)}</b>\nStatus: {escape(str(item['status']))}\n"
            f"Health: {escape(str(item.get('health') or 'n/a'))}\n"
            f"Image: {escape(str(item['image']))}\nRestarts: {item['restart_count']}"
        )
        rows = [[InlineKeyboardButton(text="Back", callback_data="containers")]]
        if item.get("restart_allowed"):
            rows.insert(0, [InlineKeyboardButton(text="Restart", callback_data=f"restart-request:{container_id}")])
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await query.answer()

    @router.callback_query(F.data.startswith("restart-request:"))
    async def restart_request(query: CallbackQuery) -> None:
        if await deny_if_needed(query):
            return
        assert isinstance(query.message, Message)
        container_id = (query.data or "").split(":", 1)[1]
        try:
            containers = await monitor.containers()
        except Exception:
            await query.answer("Docker guard is unavailable", show_alert=True)
            return
        item = next((value for value in containers if value["id"] == container_id), None)
        if item is None:
            await query.answer("Container no longer exists", show_alert=True)
            return
        name = str(item["name"])
        if name not in config.restart_allowed:
            await query.answer("Restart is not permitted for this container", show_alert=True)
            return
        token = store.create_confirmation(config.allowed_user_id, "restart", name)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Confirm restart", callback_data=f"restart-confirm:{token}")],
                [InlineKeyboardButton(text="Cancel", callback_data="containers")],
            ]
        )
        await query.message.edit_text(
            f"⚠️ Restart <b>{escape(name)}</b>? This action expires in 60 seconds.", reply_markup=keyboard
        )
        await query.answer()

    @router.callback_query(F.data.startswith("restart-confirm:"))
    async def restart_confirm(query: CallbackQuery) -> None:
        if await deny_if_needed(query):
            return
        assert isinstance(query.message, Message)
        token = (query.data or "").split(":", 1)[1]
        name = store.consume_confirmation_target(token, config.allowed_user_id, "restart")
        if name is None:
            await query.answer("Confirmation expired or already used", show_alert=True)
            return
        try:
            await monitor.restart(name)
        except Exception:
            store.audit("restart_failed", name)
            await query.message.edit_text(f"❌ Docker could not restart <b>{escape(name)}</b>.")
            await query.answer()
            return
        store.audit("container_restarted", name)
        await query.message.edit_text(f"✅ Restart requested for <b>{escape(name)}</b>.")
        await query.answer()

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher


async def run_bot(
    config: Config,
    store: Store,
    monitor: Monitor,
    pihole: PiholeClient | None = None,
) -> None:
    bot: Bot | None = None
    task: asyncio.Task[None] | None = None
    try:
        bot = Bot(config.token, default=DefaultBotProperties(parse_mode="HTML"))
        dispatcher = create_dispatcher(config, store, monitor, pihole)
        task = asyncio.create_task(
            monitor.run(lambda text: bot.send_message(config.allowed_user_id, text))
        )
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        try:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        finally:
            try:
                await monitor.close()
            finally:
                try:
                    if pihole is not None:
                        await pihole.close()
                finally:
                    if bot is not None:
                        await bot.session.close()
