# Rasptele configuration reference

Rasptele reads non-secret settings from YAML and secrets from environment variables. The application validates both sources before Telegram polling begins.

## YAML settings

The three Compose services read `/config/config.yaml`. `config.example.yaml` contains a complete example.

| Key | Type | Default | Constraints and behavior |
| --- | --- | --- | --- |
| `database_path` | string | `/data/rasptele.sqlite3` | Must be non-empty. Stores alerts, confirmations, and audit records. |
| `monitor_interval_seconds` | integer | `60` | Must be greater than zero. Controls full host, Docker, and integration reconciliation. |
| `reminder_interval_minutes` | integer | `30` | Must be greater than zero. Controls reminders for active alerts. |
| `audit_retention_days` | integer | `90` | Must be greater than zero. Controls retention for audit records and resolved incidents. |
| `alerts.disk_percent` | number | `90` | Must be greater than zero and at most `100`. |
| `alerts.temperature_celsius` | number | `80` | Must be greater than zero. |
| `containers.restart_allowed` | list of strings | `[]` | Each value is an exact Docker container name. Only listed names can be restarted. |
| `docker_guard_url` | HTTP or HTTPS URL | `http://docker-guard:8080` | Internal address of the Docker guard. |
| `integrations` | mapping | `{}` | Contains optional integration configuration. |
| `integrations.pihole.url` | HTTP or HTTPS URL | Not configured | Pi-hole v6 base URL. A trailing slash is removed. Do not append `/api`. |

`alerts`, `containers`, and `integrations` must be YAML mappings. `containers.restart_allowed` must contain trimmed, non-empty strings.

## Environment variables

| Variable | Required | Consumer | Description |
| --- | --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Bot and watchdog | `rasptele`, `rasptele-watchdog` | Token created by `@BotFather`. |
| `TELEGRAM_ALLOWED_USER_ID` | Bot and watchdog | `rasptele`, `rasptele-watchdog` | Positive numeric Telegram user ID permitted to use the bot. |
| `PIHOLE_PASSWORD` | When Pi-hole is configured | `rasptele` | Pi-hole v6 application password. |

The supplied Compose files require `PIHOLE_PASSWORD` during interpolation even when Pi-hole is disabled. Set it to `not-configured` when `integrations.pihole` is absent. Rasptele does not use the placeholder in that configuration.

## Pi-hole configuration

Rasptele supports Pi-hole v6 application-password authentication. Pi-hole v5 and regular-password authentication that also requires a time-based one-time password are not supported.

```yaml
integrations:
  pihole:
    url: http://192.168.1.2
```

Set the matching application password in `PIHOLE_PASSWORD`. An HTTP URL sends the password without transport encryption, so keep an HTTP-only Pi-hole on a trusted local network.

When Pi-hole is configured, `/pihole` reports blocking state, total queries, blocked queries, blocked percentage, blocklist size, and active clients. Disabling blocking requires a single-use confirmation and lasts five minutes. Enabling blocking takes effect immediately.

## Telegram commands

| Command | Result |
| --- | --- |
| `/start` or `/help` | Confirms that the bot is online and lists configured commands. |
| `/status` | Reports host CPU, memory, disk, temperature, throttling, and container health. |
| `/containers` | Lists containers and opens details and allowed restart actions. |
| `/pihole` | Reports Pi-hole v6 status and controls when configured. |
| `/audit` | Shows the ten most recent local audit records. |

Commands outside the configured user's private chat receive no response. Rasptele records the attempt in its local audit log.

## Process arguments

| Argument | Default | Description |
| --- | --- | --- |
| `--config <path>` | `/config/config.yaml` | YAML configuration path. |
| `--guard` | `false` | Runs the Docker guard service instead of the Telegram bot. |
| `--watchdog` | `false` | Runs the independent bot and guard watchdog. |

Invalid configuration terminates the process with a `configuration error` message before polling starts.
