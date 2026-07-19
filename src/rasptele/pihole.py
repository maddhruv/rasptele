"""Authenticated Pi-hole v6 API client with strict response validation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from math import isfinite
from typing import Any

import httpx

from .config import PiholeConfig


class PiholeError(RuntimeError):
    """Base error for safe Pi-hole integration failures."""


class PiholeAuthenticationError(PiholeError):
    """Pi-hole rejected or returned invalid authentication data."""


class PiholeResponseError(PiholeError):
    """Pi-hole returned an invalid or unsuccessful API response."""


class PiholeStatusRefreshError(PiholeError):
    """An action succeeded but its follow-up status refresh failed."""

    def __init__(self, action: str, reason_type: str) -> None:
        super().__init__(f"Pi-hole {action} succeeded but status refresh failed ({reason_type})")
        self.action = action
        self.reason_type = reason_type


@dataclass(frozen=True)
class PiholeStatus:
    blocking: str
    timer_seconds: float | None
    queries_total: int
    queries_blocked: int
    percent_blocked: float
    domains_being_blocked: int
    active_clients: int


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PiholeResponseError(f"Pi-hole returned invalid {field}")
    return value


def _number(value: Any, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise PiholeResponseError(f"Pi-hole returned invalid {field}")
    return value


def _integer(value: Any, field: str) -> int:
    number = _number(value, field)
    if isinstance(number, float) and not number.is_integer():
        raise PiholeResponseError(f"Pi-hole returned invalid {field}")
    return int(number)


class PiholeClient:
    def __init__(
        self,
        config: PiholeConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._password = config.password
        self._client = httpx.AsyncClient(
            base_url=config.url,
            timeout=timeout_seconds,
            transport=transport,
            headers={"Accept": "application/json"},
        )
        self._sid: str | None = None
        self._auth_lock = asyncio.Lock()

    @property
    def is_closed(self) -> bool:
        return self._client.is_closed

    async def _authenticate_locked(self) -> str:
        try:
            response = await self._client.post("/api/auth", json={"password": self._password})
        except httpx.HTTPError as exc:
            raise PiholeAuthenticationError("Pi-hole authentication request failed") from exc
        if response.status_code >= 400:
            raise PiholeAuthenticationError("Pi-hole authentication was rejected")
        try:
            payload = _mapping(response.json(), "authentication response")
            session = _mapping(payload.get("session"), "authentication session")
        except (ValueError, PiholeResponseError) as exc:
            raise PiholeAuthenticationError("Pi-hole returned invalid authentication data") from exc
        sid = session.get("sid")
        if session.get("valid") is not True or not isinstance(sid, str) or not sid:
            raise PiholeAuthenticationError("Pi-hole returned invalid authentication data")
        self._sid = sid
        return sid

    async def _ensure_sid(self) -> str:
        if self._sid is not None:
            return self._sid
        async with self._auth_lock:
            if self._sid is None:
                return await self._authenticate_locked()
            return self._sid

    async def _refresh_sid(self, rejected_sid: str) -> str:
        async with self._auth_lock:
            if self._sid == rejected_sid:
                self._sid = None
            if self._sid is None:
                return await self._authenticate_locked()
            return self._sid

    async def _request(
        self, method: str, path: str, *, json_body: dict[str, object] | None = None
    ) -> dict[str, Any]:
        sid = await self._ensure_sid()
        for attempt in range(2):
            try:
                response = await self._client.request(
                    method, path, json=json_body, headers={"X-FTL-SID": sid}
                )
            except httpx.HTTPError as exc:
                raise PiholeError("Pi-hole request failed") from exc
            if response.status_code == 401:
                if attempt == 1:
                    raise PiholeAuthenticationError("Pi-hole session was rejected")
                sid = await self._refresh_sid(sid)
                continue
            if response.status_code >= 400:
                raise PiholeError(f"Pi-hole request failed with HTTP {response.status_code}")
            try:
                return _mapping(response.json(), "API response")
            except ValueError as exc:
                raise PiholeResponseError("Pi-hole returned invalid JSON") from exc
        raise AssertionError("bounded Pi-hole retry loop exhausted")

    @staticmethod
    def _parse_blocking(payload: dict[str, Any]) -> tuple[str, float | None]:
        blocking = payload.get("blocking")
        if not isinstance(blocking, str) or not blocking:
            raise PiholeResponseError("Pi-hole returned invalid blocking state")
        timer = payload.get("timer")
        if timer is None:
            return blocking, None
        return blocking, float(_number(timer, "blocking timer"))

    @staticmethod
    def _parse_status(
        stats: dict[str, Any], blocking: dict[str, Any]
    ) -> PiholeStatus:
        queries = _mapping(stats.get("queries"), "queries")
        clients = _mapping(stats.get("clients"), "clients")
        gravity = _mapping(stats.get("gravity"), "gravity")
        blocking_state, timer = PiholeClient._parse_blocking(blocking)
        return PiholeStatus(
            blocking=blocking_state,
            timer_seconds=timer,
            queries_total=_integer(queries.get("total"), "queries.total"),
            queries_blocked=_integer(queries.get("blocked"), "queries.blocked"),
            percent_blocked=float(_number(queries.get("percent_blocked"), "queries.percent_blocked")),
            domains_being_blocked=_integer(
                gravity.get("domains_being_blocked"), "gravity.domains_being_blocked"
            ),
            active_clients=_integer(clients.get("active"), "clients.active"),
        )

    async def status(self) -> PiholeStatus:
        stats, blocking = await asyncio.gather(
            self._request("GET", "/api/stats/summary"),
            self._request("GET", "/api/dns/blocking"),
        )
        return self._parse_status(stats, blocking)

    async def _set_blocking(
        self, *, enabled: bool, seconds: int | None, action: str
    ) -> PiholeStatus:
        body: dict[str, object] = {"blocking": enabled}
        if seconds is not None:
            body["timer"] = seconds
        mutation = await self._request("POST", "/api/dns/blocking", json_body=body)
        returned_state, _ = self._parse_blocking(mutation)
        expected_state = "enabled" if enabled else "disabled"
        if returned_state != expected_state:
            raise PiholeResponseError("Pi-hole returned unexpected blocking state")
        try:
            return await self.status()
        except PiholeError as exc:
            raise PiholeStatusRefreshError(action, type(exc).__name__) from exc

    async def disable(self, seconds: int = 300) -> PiholeStatus:
        if seconds <= 0:
            raise ValueError("disable duration must be positive")
        return await self._set_blocking(enabled=False, seconds=seconds, action="disable")

    async def enable(self) -> PiholeStatus:
        return await self._set_blocking(enabled=True, seconds=None, action="enable")

    async def close(self) -> None:
        sid = self._sid
        self._sid = None
        if sid is not None and not self._client.is_closed:
            try:
                await self._client.delete("/api/auth", headers={"X-FTL-SID": sid})
            except httpx.HTTPError:
                pass
        await self._client.aclose()
