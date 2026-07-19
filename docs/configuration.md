# Rasptele configuration reference

Rasptele reads configuration exclusively from environment variables. Docker Compose reads `.env`; Portainer and Coolify provide equivalent environment-variable forms. Invalid values stop the affected service before it connects to Telegram or Docker.

| Variable | Required | Default | Consumer | Description |
| --- | --- | --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Yes | — | bot, watchdog | Token from `@BotFather`. |
| `TELEGRAM_ALLOWED_USER_ID` | Yes | — | bot, watchdog | Positive numeric Telegram user ID. |
| `PIHOLE_URL` | With password | empty | bot | Pi-hole v6 HTTP(S) base URL without `/api`. |
| `PIHOLE_PASSWORD` | With URL | empty | bot | Pi-hole v6 application password. |
| `RASPTELE_RESTART_ALLOWED` | No | empty | bot, guard | Comma-separated exact container names, such as `pihole,jellyfin`. |
| `RASPTELE_MONITOR_INTERVAL_SECONDS` | No | `60` | bot, watchdog | Positive reconciliation interval. |
| `RASPTELE_REMINDER_INTERVAL_MINUTES` | No | `30` | bot | Positive active-alert reminder interval. |
| `RASPTELE_AUDIT_RETENTION_DAYS` | No | `90` | bot | Positive retention period. |
| `RASPTELE_DISK_PERCENT` | No | `90` | bot | Disk threshold greater than zero and at most 100. |
| `RASPTELE_TEMPERATURE_CELSIUS` | No | `80` | bot | Positive temperature threshold. |

Missing or empty restart allowlist denies all restarts. Values are trimmed, empty entries ignored, and duplicates collapsed.

## Pi-hole

Leave both Pi-hole variables empty to disable integration. To enable Pi-hole v6:

```dotenv
PIHOLE_URL=http://192.168.1.2
PIHOLE_PASSWORD=<APPLICATION_PASSWORD>
```

Setting only one variable is an error. HTTP sends the application password without transport encryption, so use it only on a trusted local network. `/pihole` reports live metrics and provides confirmed temporary disable and immediate enable controls.

## Process modes

`--guard` runs the Docker guard and `--watchdog` runs the independent watchdog. Deployment users do not need to set these; `compose.yaml` supplies them to the correct services.
