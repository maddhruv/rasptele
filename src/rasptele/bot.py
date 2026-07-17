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


def create_dispatcher(config: Config, store: Store, monitor: Monitor) -> Dispatcher:
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
        await message.answer("Rasptele is online. Use /status, /containers, or /audit.")

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


async def run_bot(config: Config, store: Store, monitor: Monitor) -> None:
    bot = Bot(config.token, default=DefaultBotProperties(parse_mode="HTML"))
    dispatcher = create_dispatcher(config, store, monitor)
    task = asyncio.create_task(monitor.run(lambda text: bot.send_message(config.allowed_user_id, text)))
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await monitor.close()
        await bot.session.close()
