import asyncio
import json
import unittest
from math import inf, nan

import httpx

from rasptele.config import PiholeConfig
from rasptele.pihole import (
    PiholeAuthenticationError,
    PiholeClient,
    PiholeResponseError,
    PiholeStatus,
    PiholeStatusRefreshError,
)

AUTH_RESPONSE = {"session": {"valid": True, "sid": "session-one", "validity": 1800}}
STATS_RESPONSE = {
    "queries": {"total": 1000, "blocked": 200, "percent_blocked": 20.0},
    "clients": {"active": 5},
    "gravity": {"domains_being_blocked": 150000},
}
BLOCKING_RESPONSE = {"blocking": "enabled", "timer": None}


def response(status: int, data: object) -> httpx.Response:
    return httpx.Response(
        status, content=json.dumps(data).encode(), headers={"content-type": "application/json"}
    )


class ScriptedTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self.handler = handler
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return await self.handler(request)


class PiholeClientTests(unittest.IsolatedAsyncioTestCase):
    def config(self, password: str = "application-password") -> PiholeConfig:
        return PiholeConfig("http://pi.hole", password)

    async def test_status_authenticates_and_reuses_sid(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/auth" and request.method == "POST":
                self.assertEqual(json.loads(request.content), {"password": "application-password"})
                return response(200, AUTH_RESPONSE)
            if request.url.path == "/api/auth":
                return response(204, {})
            self.assertEqual(request.headers["X-FTL-SID"], "session-one")
            if request.url.path == "/api/stats/summary":
                return response(200, STATS_RESPONSE)
            return response(200, BLOCKING_RESPONSE)

        transport = ScriptedTransport(handler)
        client = PiholeClient(self.config(), transport=transport)
        self.addAsyncCleanup(client.close)
        expected = PiholeStatus("enabled", None, 1000, 200, 20.0, 150000, 5)
        self.assertEqual(await client.status(), expected)
        self.assertEqual(await client.status(), expected)
        self.assertEqual([r.url.path for r in transport.requests].count("/api/auth"), 1)

    async def test_one_401_reauthenticates_and_retries_once(self):
        auth_count = 0
        stats_count = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal auth_count, stats_count
            if request.url.path == "/api/auth":
                auth_count += 1
                sid = f"session-{auth_count}"
                return response(200, {"session": {"valid": True, "sid": sid}})
            if request.url.path == "/api/stats/summary":
                stats_count += 1
                if stats_count == 1:
                    self.assertEqual(request.headers["X-FTL-SID"], "session-1")
                    return response(401, {"error": {"key": "unauthorized"}})
                self.assertEqual(request.headers["X-FTL-SID"], "session-2")
                return response(200, STATS_RESPONSE)
            return response(200, BLOCKING_RESPONSE)

        client = PiholeClient(self.config(), transport=ScriptedTransport(handler))
        self.addAsyncCleanup(client.close)
        self.assertEqual((await client.status()).queries_total, 1000)
        self.assertEqual(auth_count, 2)
        self.assertEqual(stats_count, 2)

    async def test_second_401_raises_authentication_error(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/auth":
                return response(200, AUTH_RESPONSE)
            return response(401, {"error": {"key": "unauthorized"}})

        client = PiholeClient(self.config(), transport=ScriptedTransport(handler))
        self.addAsyncCleanup(client.close)
        with self.assertRaises(PiholeAuthenticationError):
            await client.status()

    async def test_concurrent_initial_requests_create_one_session(self):
        auth_count = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal auth_count
            if request.url.path == "/api/auth":
                auth_count += 1
                await asyncio.sleep(0.01)
                return response(200, AUTH_RESPONSE)
            if request.url.path == "/api/stats/summary":
                return response(200, STATS_RESPONSE)
            return response(200, BLOCKING_RESPONSE)

        client = PiholeClient(self.config(), transport=ScriptedTransport(handler))
        self.addAsyncCleanup(client.close)
        await asyncio.gather(client.status(), client.status())
        self.assertEqual(auth_count, 1)

    async def test_concurrent_stale_sid_responses_create_one_replacement_session(self):
        auth_count = 0
        stale_requests = 0
        refreshed_requests = 0
        both_stale = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal auth_count, stale_requests, refreshed_requests
            if request.url.path == "/api/auth":
                auth_count += 1
                return response(
                    200, {"session": {"valid": True, "sid": f"session-{auth_count}"}}
                )
            sid = request.headers["X-FTL-SID"]
            if sid == "session-1":
                stale_requests += 1
                if stale_requests == 2:
                    both_stale.set()
                await both_stale.wait()
                return response(401, {"error": {"key": "unauthorized"}})
            self.assertEqual(sid, "session-2")
            refreshed_requests += 1
            if request.url.path == "/api/stats/summary":
                return response(200, STATS_RESPONSE)
            return response(200, BLOCKING_RESPONSE)

        client = PiholeClient(self.config(), transport=ScriptedTransport(handler))
        self.addAsyncCleanup(client.close)
        await asyncio.gather(client.status(), client.status())
        self.assertEqual(auth_count, 2)
        self.assertEqual(stale_requests, 2)
        self.assertEqual(refreshed_requests, 4)

    async def test_rejects_invalid_authentication_response(self):
        invalid_auth = [
            {},
            {"session": []},
            {"session": {"valid": False, "sid": "session-one"}},
            {"session": {"valid": True, "sid": ""}},
            {"session": {"valid": True, "sid": 7}},
        ]
        for payload in invalid_auth:
            with self.subTest(payload=payload):
                async def handler(_: httpx.Request, payload=payload) -> httpx.Response:
                    return response(200, payload)

                client = PiholeClient(self.config(), transport=ScriptedTransport(handler))
                with self.assertRaises(PiholeAuthenticationError):
                    await client.status()
                await client.close()

    async def test_rejects_malformed_status_fields(self):
        invalid_values = [None, True, "1000", nan, inf]
        for value in invalid_values:
            with self.subTest(value=value):
                stats = json.loads(json.dumps(STATS_RESPONSE))
                stats["queries"]["total"] = value

                async def handler(request: httpx.Request, stats=stats) -> httpx.Response:
                    if request.url.path == "/api/auth":
                        return response(200, AUTH_RESPONSE)
                    if request.url.path == "/api/stats/summary":
                        return response(200, stats)
                    return response(200, BLOCKING_RESPONSE)

                client = PiholeClient(self.config(), transport=ScriptedTransport(handler))
                with self.assertRaises(PiholeResponseError):
                    await client.status()
                await client.close()

    async def test_rejects_malformed_numeric_fields_and_timer(self):
        cases = [
            ("queries", "blocked"),
            ("queries", "percent_blocked"),
            ("clients", "active"),
            ("gravity", "domains_being_blocked"),
        ]
        for section, field in cases:
            with self.subTest(section=section, field=field):
                stats = json.loads(json.dumps(STATS_RESPONSE))
                stats[section][field] = True

                async def handler(request: httpx.Request, stats=stats) -> httpx.Response:
                    if request.url.path == "/api/auth":
                        return response(200, AUTH_RESPONSE)
                    if request.url.path == "/api/stats/summary":
                        return response(200, stats)
                    return response(200, BLOCKING_RESPONSE)

                client = PiholeClient(self.config(), transport=ScriptedTransport(handler))
                with self.assertRaises(PiholeResponseError):
                    await client.status()
                await client.close()

        async def timer_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/auth":
                return response(200, AUTH_RESPONSE)
            if request.url.path == "/api/stats/summary":
                return response(200, STATS_RESPONSE)
            return response(200, {"blocking": "disabled", "timer": True})

        client = PiholeClient(self.config(), transport=ScriptedTransport(timer_handler))
        with self.assertRaises(PiholeResponseError):
            await client.status()
        await client.close()

    async def test_disable_sends_timer_and_returns_fresh_status(self):
        mutation_count = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal mutation_count
            if request.url.path == "/api/auth":
                return response(200, AUTH_RESPONSE)
            if request.url.path == "/api/dns/blocking" and request.method == "POST":
                mutation_count += 1
                self.assertEqual(json.loads(request.content), {"blocking": False, "timer": 300})
                return response(200, {"blocking": "disabled", "timer": 300})
            if request.url.path == "/api/stats/summary":
                return response(200, STATS_RESPONSE)
            return response(200, {"blocking": "disabled", "timer": 299})

        client = PiholeClient(self.config(), transport=ScriptedTransport(handler))
        self.addAsyncCleanup(client.close)
        status = await client.disable(300)
        self.assertEqual(status.blocking, "disabled")
        self.assertEqual(status.timer_seconds, 299)
        self.assertEqual(mutation_count, 1)

    async def test_enable_sends_immediate_payload(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/auth":
                return response(200, AUTH_RESPONSE)
            if request.url.path == "/api/dns/blocking" and request.method == "POST":
                self.assertEqual(json.loads(request.content), {"blocking": True})
                return response(200, {"blocking": "enabled", "timer": None})
            if request.url.path == "/api/stats/summary":
                return response(200, STATS_RESPONSE)
            return response(200, BLOCKING_RESPONSE)

        client = PiholeClient(self.config(), transport=ScriptedTransport(handler))
        self.addAsyncCleanup(client.close)
        self.assertEqual((await client.enable()).blocking, "enabled")

    async def test_malformed_mutation_response_skips_status_refresh(self):
        for payload in [{}, {"blocking": 7}, {"blocking": "enabled", "timer": None}]:
            with self.subTest(payload=payload):
                paths: list[str] = []

                async def handler(request: httpx.Request, payload=payload) -> httpx.Response:
                    paths.append(request.url.path)
                    if request.url.path == "/api/auth":
                        return response(200, AUTH_RESPONSE)
                    return response(200, payload)

                client = PiholeClient(self.config(), transport=ScriptedTransport(handler))
                with self.assertRaises(PiholeResponseError):
                    await client.disable(300)
                self.assertEqual(paths, ["/api/auth", "/api/dns/blocking"])
                await client.close()

    async def test_mutation_success_then_refresh_failure_has_distinct_safe_error(self):
        mutation_count = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal mutation_count
            if request.url.path == "/api/auth":
                return response(200, AUTH_RESPONSE)
            if request.url.path == "/api/dns/blocking" and request.method == "POST":
                mutation_count += 1
                return response(200, {"blocking": "disabled", "timer": 300})
            return response(503, {"error": {"key": "unavailable"}})

        password = "never-leak-this"
        client = PiholeClient(self.config(password), transport=ScriptedTransport(handler))
        self.addAsyncCleanup(client.close)
        with self.assertRaises(PiholeStatusRefreshError) as raised:
            await client.disable(300)
        self.assertEqual(raised.exception.action, "disable")
        self.assertTrue(raised.exception.reason_type)
        self.assertNotIn(password, str(raised.exception))
        self.assertEqual(mutation_count, 1)

    async def test_close_logs_out_and_closes_after_logout_failure(self):
        logout_count = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal logout_count
            if request.url.path == "/api/auth" and request.method == "POST":
                return response(200, AUTH_RESPONSE)
            if request.url.path == "/api/auth" and request.method == "DELETE":
                logout_count += 1
                return response(503, {"error": {"key": "unavailable"}})
            if request.url.path == "/api/stats/summary":
                return response(200, STATS_RESPONSE)
            return response(200, BLOCKING_RESPONSE)

        client = PiholeClient(self.config(), transport=ScriptedTransport(handler))
        await client.status()
        await client.close()
        self.assertEqual(logout_count, 1)
        self.assertTrue(client.is_closed)
