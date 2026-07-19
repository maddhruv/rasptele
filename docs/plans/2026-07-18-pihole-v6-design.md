# Pi-hole v6 Integration Design Spec

## Summary

Rasptele v0.2 adds Pi-hole v6 as its first service integration. The owner can view live Pi-hole statistics, disable blocking for five minutes through a two-step confirmation, re-enable blocking immediately, and receive proactive outage/recovery alerts.

The integration calls Pi-hole directly over its HTTP API. It uses a Pi-hole application password from the environment, keeps the resulting session ID only in memory, and does not expand Docker Guard authority.

## Context

Rasptele v0.1 exposes host and Docker state through Telegram. `src/rasptele/bot.py` owns commands and callbacks, `src/rasptele/monitor.py` reconciles incidents, `src/rasptele/config.py` loads YAML and environment secrets, and `src/rasptele/store.py` provides durable confirmations, incidents, notifications, and audit records.

`config.example.yaml` contains an unused `integrations: {}` placeholder. `httpx` is already a runtime dependency. The deployment has three services, but only the `rasptele` service needs Pi-hole credentials.

This design targets Pi-hole v6 only. API contracts were verified against the official Pi-hole FTL v6 OpenAPI definitions and implementation:

- `POST /api/auth` authenticates an application password.
- `X-FTL-SID` authenticates subsequent requests.
- `GET /api/stats/summary` returns current query, client, and gravity statistics.
- `GET /api/dns/blocking` returns current blocking state and timer.
- `POST /api/dns/blocking` changes blocking state and accepts an optional timer.
- `DELETE /api/auth` invalidates the current session.

## Design

### Architecture

```text
Telegram owner
   │ /pihole and inline callbacks
   ▼
bot.py ────────────────┐
                      │ live status/actions
monitor.py ────────────┤ periodic health checks
                      ▼
                 PiholeClient
                      │ Pi-hole v6 HTTP API
                      ▼
                Pi-hole LAN URL

config.yaml ── URL
environment ── application password
store.py ───── confirmations, incidents, notifications, audit
```

`src/rasptele/main.py` creates one optional `PiholeClient`. The same instance is injected into `Monitor` and `run_bot`, so Telegram requests and monitoring share one in-memory Pi-hole session. `run_bot` closes the client during graceful shutdown.

Docker Guard remains unchanged. Pi-hole API calls never pass through the Docker socket sidecar.

### Components

| Component | Responsibility | Dependencies | File path |
| --- | --- | --- | --- |
| `PiholeConfig` | Hold enabled Pi-hole URL and application password | Environment and parsed YAML | `src/rasptele/config.py` |
| `PiholeClient` | Authenticate, retry once after HTTP 401, parse status, toggle blocking, log out | `PiholeConfig`, `httpx` | `src/rasptele/pihole.py` |
| `PiholeStatus` | Typed live status returned to bot handlers | Validated Pi-hole responses | `src/rasptele/pihole.py` |
| Pi-hole bot handlers | Render `/pihole`, confirm timed disable, enable immediately, refresh message | `PiholeClient`, `Store`, existing authorization | `src/rasptele/bot.py` |
| Pi-hole monitor check | Reconcile outage and recovery incidents | `PiholeClient`, `Store` outbox/incidents | `src/rasptele/monitor.py` |
| Application wiring | Create/inject/close optional client | `Config`, `PiholeClient`, `Monitor`, bot | `src/rasptele/main.py`, `src/rasptele/bot.py` |
| Deployment config | Pass `PIHOLE_PASSWORD` only to the main bot | Runtime secret providers | `compose.yaml`, `compose.coolify.yaml`, `compose.portainer.yaml` |
| Operator docs | Configure Pi-hole v6 URL/application password and exercise commands | Shipped config and Compose files | `README.md`, `config.example.yaml`, `.env.example` |
| Release metadata | Report/package v0.2.0 | Build and release workflow | `pyproject.toml`, `src/rasptele/__init__.py`, Compose image tags |

### Configuration model

```python
@dataclass(frozen=True)
class PiholeConfig:
    url: str
    password: str

@dataclass(frozen=True)
class Config:
    # Existing fields omitted.
    pihole: PiholeConfig | None
```

YAML enables the integration:

```yaml
integrations:
  pihole:
    url: http://192.168.1.2
```

The application password is supplied separately:

```dotenv
PIHOLE_PASSWORD=replace-with-pihole-application-password
```

Validation rules:

- Missing `integrations` or missing `integrations.pihole` disables Pi-hole cleanly.
- `integrations` and `integrations.pihole` must be mappings when present.
- `integrations.pihole.url` must be an HTTP or HTTPS URL with a network location.
- The URL is normalized by removing its trailing slash.
- An enabled Pi-hole integration requires a non-empty `PIHOLE_PASSWORD` for the main bot process.
- Docker Guard and watchdog load the shared YAML without requiring or receiving `PIHOLE_PASSWORD`.
- Configuration errors never include the password.

`load_config` gains an explicit integration-secret requirement flag. Main bot loading enables it; Docker Guard and watchdog loading disable it. The YAML URL remains visible to all three services because they share `config.yaml`, but only the bot receives the secret.

Local Compose stops passing the complete `.env` file to both Telegram processes. It uses Compose interpolation to pass only `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USER_ID` to the watchdog, while the main bot additionally receives `PIHOLE_PASSWORD`. Coolify and Portainer follow the same least-privilege split.

### Pi-hole client interfaces

```python
@dataclass(frozen=True)
class PiholeStatus:
    blocking: str
    timer_seconds: float | None
    queries_total: int
    queries_blocked: int
    percent_blocked: float
    domains_being_blocked: int
    active_clients: int

class PiholeError(RuntimeError): ...
class PiholeAuthenticationError(PiholeError): ...
class PiholeResponseError(PiholeError): ...
class PiholeStatusRefreshError(PiholeError):
    action: str
    reason_type: str

class PiholeClient:
    def __init__(
        self,
        config: PiholeConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10.0,
    ) -> None: ...
    async def status(self) -> PiholeStatus: ...
    async def disable(self, seconds: int = 300) -> PiholeStatus: ...
    async def enable(self) -> PiholeStatus: ...
    async def close(self) -> None: ...
```

The client always owns the `httpx.AsyncClient` it constructs from `config.url`, the 10-second default timeout, and optional test transport. Callers own `PiholeClient` and call `close()` exactly once. `disable()` and `enable()` require the mutation response to contain the requested `blocking` state, then call `status()` and return that fresh combined status. They never synthesize query statistics from pre-action data.

If the validated mutation succeeds but `status()` fails, the method raises `PiholeStatusRefreshError`. Its `action` is `disable` or `enable`; `reason_type` contains only the underlying exception class name. It contains no response body, URL, password, or SID. A rejected HTTP mutation or malformed/wrong-state mutation response raises the ordinary request/auth/response exception instead, so the handler does not claim action success.

`blocking` accepts Pi-hole's returned string. Telegram treats only `enabled` as enabled and only `disabled` as disabled; other values render as unavailable and expose no action button.

`status()` combines these responses:

```json
{
  "queries": {
    "total": 1000,
    "blocked": 200,
    "percent_blocked": 20.0
  },
  "clients": {"active": 5},
  "gravity": {"domains_being_blocked": 150000}
}
```

```json
{"blocking": "enabled", "timer": null}
```

Timed disable sends:

```json
{"blocking": false, "timer": 300}
```

Immediate enable sends:

```json
{"blocking": true}
```

The client validates required response objects and numeric/string fields. Missing, boolean-as-number, non-finite, or otherwise malformed values raise `PiholeResponseError`.

### Authentication and session lifecycle

1. Before an authenticated request, the client ensures a session exists.
2. Authentication sends `POST /api/auth` with `{"password": password}`.
3. A valid response must contain `session.valid == true` and a non-empty string `session.sid`.
4. The SID is stored only on the `PiholeClient` instance and sent through `X-FTL-SID`.
5. An async lock prevents concurrent monitoring and Telegram requests from creating duplicate sessions.
6. Each request captures the SID it sent. If it returns HTTP 401, the client enters the authentication lock and clears the session only when the current SID still equals that rejected SID. If another request already installed a newer SID, the waiting request reuses it instead of authenticating again.
7. The request retries once using the current refreshed SID.
8. A second HTTP 401 raises `PiholeAuthenticationError`; no unbounded retry occurs.
9. `close()` attempts `DELETE /api/auth` with the current SID, discards the SID even if logout fails, and closes the HTTP client.
10. Passwords and SIDs never enter SQLite, audit details, Telegram messages, or logs.

### Telegram interface

`/pihole` is protected by the existing owner/private-chat authorization check. When configured and available, it sends:

```text
🕳 Pi-hole
Blocking: enabled
Queries: 1,000
Blocked: 200 (20.0%)
Blocklist domains: 150,000
Active clients: 5
```

Keyboard behavior:

| Live state | Button | Callback | Behavior |
| --- | --- | --- | --- |
| `enabled` | Disable for 5 minutes | `pihole-disable-request` | Creates confirmation and replaces message with warning |
| `disabled` | Enable now | `pihole-enable` | Enables immediately, audits, refreshes live status |
| other/unavailable | Refresh | `pihole-refresh` | Fetches live state again; no mutation offered |
| any known state | Refresh | `pihole-refresh` | Fetches live state and edits the same message |

Disable confirmation uses `Store.create_confirmation` with action `pihole_disable` and target `300`. The confirmation callback contains only the generated token:

```text
pihole-disable-confirm:<token>
```

The token remains owner-bound, single-use, and valid for 60 seconds. Successful confirmation calls `disable(300)`. That method posts the mutation and performs a fresh combined status read. The handler records `pihole_disabled` with detail `seconds=300`, then edits the confirmation message with the returned live status and normal Pi-hole keyboard. An expired or reused token performs no API call.

`pihole-enable` calls `enable()` immediately because it restores protection. The method posts the mutation and performs a fresh combined status read. The handler records `pihole_enabled`, then edits the same message with the returned status.

If Pi-hole returns the requested mutation state but the follow-up status read fails, the client raises `PiholeStatusRefreshError`. The handler records both the successful action (`pihole_disabled` with `seconds=300`, or `pihole_enabled`) and `pihole_disable_status_failed` or `pihole_enable_status_failed` with `reason_type`. It edits the message to `Pi-hole was updated, but its current status could not be refreshed.` It must not retry the mutation. Tests distinguish a failed mutation from this post-mutation refresh failure.

When Pi-hole is not configured, `/pihole` replies `Pi-hole integration is not configured.` When a request fails, the bot replies or edits with `Pi-hole is unavailable. Try again later.` It records `pihole_status_failed`, `pihole_disable_failed`, or `pihole_enable_failed` using exception class names only.

`/start` and `/help` include `/pihole` only when the integration is configured.

### Monitoring flow

`Monitor` receives `PiholeClient | None`.

1. Existing host checks run first.
2. Docker reconciliation moves into an isolated block that records Docker Guard failure without returning from `Monitor.check()`. When the guard is unavailable, existing container incidents remain unchanged because absence cannot be distinguished from an unavailable inventory.
3. When a Pi-hole client exists, `Monitor.check()` calls `status()` once per reconciliation even when Docker Guard failed.
4. Success reconciles incident key `pihole` as recovered with detail `Pi-hole service restored`.
5. `PiholeAuthenticationError` reconciles `pihole` as active with public detail `Pi-hole service is unavailable` and audits `pihole_auth_failed` only when the incident opens or reaches a reminder transition.
6. Other `PiholeError` values use the same public incident detail and audit `pihole_check_failed` only on open/reminder transitions.
7. Existing durable outbox and reminder behavior delivers outage, reminder, and recovery messages.
8. Pi-hole failure does not prevent host or Docker checks from completing.

`Monitor._reconcile` returns its transition so Pi-hole-specific audit events can be rate-limited to incident open/reminder transitions.

### Deployment and versioning

All three Compose variants pass `PIHOLE_PASSWORD` only to `rasptele`:

- `compose.yaml` reads `.env` through interpolation and lists each environment variable explicitly.
- `compose.coolify.yaml` declares `${PIHOLE_PASSWORD:?required}` only under `rasptele`.
- `compose.portainer.yaml` declares `${PIHOLE_PASSWORD:?required}` only under `rasptele`.

Because Compose cannot make a variable conditionally required based on YAML content, deployment templates require `PIHOLE_PASSWORD` for v0.2. Operators not enabling Pi-hole may set a non-secret placeholder; application configuration ignores it when `integrations.pihole` is absent. README examples enable Pi-hole and require a real application password.

Version values and image references move from `0.1.0` to `0.2.0`. The Portainer image becomes usable only after the `v0.2.0` release publishes GHCR manifests.

## Error Handling

| Failure | Internal behavior | Telegram/alert behavior | Audit behavior |
| --- | --- | --- | --- |
| Pi-hole absent from config | Do not create client or monitor check | `/pihole`: not configured | None |
| Configured URL invalid | Fail startup with `ConfigurationError` | Bot does not start | Startup error excludes secret |
| Password missing for bot | Fail startup with `ConfigurationError` | Bot does not start | Startup error excludes secret |
| Login rejected/malformed | Raise `PiholeAuthenticationError` | Generic unavailable message/alert | Auth failure class only |
| Authenticated request returns one 401 | Clear matching SID, reauthenticate, retry once | Transparent if retry succeeds | None |
| Retried request returns 401 | Raise `PiholeAuthenticationError` | Generic unavailable message/alert | Auth failure class only |
| Timeout, DNS, connection, or 5xx | Raise `PiholeError` | Generic unavailable message/alert | Error class only |
| Malformed stats/blocking response | Raise `PiholeResponseError` | Generic unavailable message/alert | Error class only |
| Disable/enable API failure | Leave live state unknown; do not claim success | Generic unavailable message | Action-specific failure event |
| Mutation returns malformed or wrong blocking state | Raise `PiholeResponseError`; never retry mutation | Generic unavailable message | Action-specific failure event only |
| Mutation succeeds; follow-up status fails | Raise `PiholeStatusRefreshError(action, reason_type)`; never retry mutation | `Pi-hole was updated, but its current status could not be refreshed.` | Successful action event plus action-specific status-failure event |
| Confirmation expired/reused/wrong user | No API request | Existing confirmation error | Existing unauthorized/confirmation behavior |
| Logout failure | Discard SID and close client | No shutdown alert | No secret or SID logged |

## Testing Strategy

### Configuration tests

- No Pi-hole YAML and no password produces `config.pihole is None`.
- Valid HTTP and HTTPS URLs plus password produce normalized `PiholeConfig`.
- Non-mapping `integrations`, non-mapping `pihole`, missing URL, invalid URL, and empty URL fail.
- Configured Pi-hole without `PIHOLE_PASSWORD` fails for the main bot.
- Configured Pi-hole loads without the secret for guard/watchdog modes.
- Password never appears in raised error strings.

### Pi-hole client tests

Use `httpx.MockTransport` to assert exact requests and responses:

- Login body is `{"password": "..."}` and status requests use `X-FTL-SID`.
- Multiple calls reuse one SID.
- One HTTP 401 triggers exactly one reauthentication and one request retry.
- A second HTTP 401 raises `PiholeAuthenticationError`.
- Concurrent first calls create one session.
- Concurrent requests rejected with the same stale SID create one replacement session, conditionally preserve a newer SID, and each retry at most once.
- `status()` parses exact stats/blocking fields into `PiholeStatus`.
- Missing objects/fields, wrong field types, boolean numeric fields, and non-finite values fail.
- `disable()` sends `{"blocking": false, "timer": 300}`.
- `enable()` sends `{"blocking": true}`.
- Disable/enable rejects mutation responses with missing, non-string, or unexpected `blocking` values and does not perform the follow-up status read.
- A validated mutation followed by failed status read raises `PiholeStatusRefreshError` with safe `action`/`reason_type` and sends the mutation exactly once.
- `close()` deletes the session and closes even when logout fails.

### Bot tests

Feed aiogram updates through the dispatcher with mocked Pi-hole client:

- Authorized private `/pihole` renders all metrics and correct enabled keyboard.
- Disabled state renders **Enable now** and timer when present.
- Unknown state renders no mutation button.
- Unauthorized user/group receives no status or action response.
- Disable request creates the expected server-side confirmation.
- Valid confirmation calls `disable(300)` once, audits success, and refreshes.
- Expired/reused confirmation performs no API call.
- Enable calls `enable()` without confirmation, audits success, and refreshes.
- Disable/enable uses the fresh status returned after mutation to edit the same Telegram message.
- A successful mutation followed by failed status refresh does not repeat the mutation, records both success and refresh-failure audit events, and shows the distinct updated-but-not-refreshed message.
- Status/action failures expose no URL, password, or SID and audit the correct event.
- Missing configuration returns the explicit not-configured message.

### Monitor tests

- Successful Pi-hole check leaves no incident.
- First failure creates one durable outage notification.
- Repeated failure before reminder creates no duplicate notification or Pi-hole-specific audit.
- Authentication failure and transport failure use distinct audit event types.
- Recovery creates one durable recovery notification.
- Pi-hole failure does not suppress host/container reconciliation.
- Docker Guard failure does not suppress Pi-hole reconciliation or recover active container incidents from an unavailable inventory.
- No configured client makes no Pi-hole request.

### Deployment and release checks

- Local, Coolify, and Portainer Compose render with Telegram and Pi-hole variables.
- Rendered configs contain `PIHOLE_PASSWORD` under `rasptele` only.
- Ruff, mypy, and the full unittest suite pass.
- Docker image builds and reports package version `0.2.0`.
- README commands and configuration keys match shipped files.

## Migration Path

1. Deploy code and config support without changing the existing SQLite schema.
2. Create a Pi-hole v6 application password in the Pi-hole UI.
3. Add `integrations.pihole.url` to the persistent `config.yaml`.
4. Add `PIHOLE_PASSWORD` to the deployment's runtime secrets.
5. Redeploy all services with the v0.2 Compose definition.
6. Test `/pihole`, timed disable, immediate enable, outage alert, and recovery alert.
7. Publish `v0.2.0`; Portainer users then update to the `0.2.0` image.

Rollback verification:

1. Redeploy the v0.1.1 Git revision or `0.1.1` image using the corresponding v0.1 Compose file.
2. Remove `integrations.pihole` from `config.yaml` and remove `PIHOLE_PASSWORD` from runtime secrets, or leave both values unused until the next maintenance window.
3. Confirm `/status`, `/containers`, and `/audit` still work and `/pihole` is absent because v0.1 has no handler.
4. Confirm the existing SQLite database opens without migration because v0.2 adds no tables or columns.

## Deferred Work

- Pi-hole v5 compatibility.
- Two-factor authentication tokens; application password is required instead.
- Permanent blocking disable.
- Per-domain allow/deny operations and unblock-request workflow.
- Historical charts, top domains, top clients, and query log browsing.
- qBittorrent, Jellyfin, Coolify API, OpenWrt, and new-device integrations.
- Docker Guard authentication and broader v0.1 security hardening.

## Decision Log

| Decision | Options considered | Chosen | Rationale |
| --- | --- | --- | --- |
| Pi-hole API generation | v5, v6, both | v6 only | Matches deployed Pi-hole and avoids dual legacy/current APIs. |
| v0.2 feature boundary | Stats only, stats/actions, full domain management | Stats plus timed disable/immediate enable | Useful daily control without expanding into unblock workflow data modeling. |
| Disable duration | Permanent, configurable, fixed timer | Fixed five minutes | Safe default; Pi-hole automatically restores protection. |
| Disable authorization | Immediate, Telegram confirmation | Existing two-step confirmation | Disabling protection is destructive and must remain owner-bound and single-use. |
| Enable authorization | Confirmation, immediate | Immediate | Enabling restores protection and does not need destructive-action friction. |
| Pi-hole credential | Web UI password, application password, either in docs | Application password required in docs | API-specific, independently rotatable, and does not require 2FA tokens. |
| Credential storage | YAML, SQLite, environment | Environment only | Matches existing secret model and avoids persistence/audit exposure. |
| Pi-hole address | Docker service name, public domain, stable LAN URL | Configured stable LAN URL | Works across local Compose, Coolify, Portainer, and separate Docker networks. |
| Session storage | Per request, memory, SQLite | Shared in-memory session | Reuses Pi-hole session without persisting sensitive tokens. |
| Expired session handling | Fail immediately, one retry, unbounded retry | Reauthenticate and retry once | Handles normal expiry without retry loops. |
| Status freshness | Cache, live fetch | Live fetch | User sees current protection and metrics; monitoring already supplies periodic calls. |
| Service monitoring | On-demand only, proactive | Proactive outage/recovery | Matches core control-plane alert behavior. |
| Pi-hole transport path | Docker Guard, direct HTTP | Direct HTTP | Guard stays narrowly scoped to Docker; Pi-hole already provides an authenticated API. |
