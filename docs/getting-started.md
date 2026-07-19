# Deploy your first Rasptele bot

This tutorial deploys Rasptele from a local checkout and verifies host monitoring through Telegram. It leaves the optional Pi-hole integration disabled.

## What you'll need

- A 64-bit Raspberry Pi running Linux
- Docker Engine with the Docker Compose plugin
- Outbound access to `api.telegram.org`
- A Telegram account

Confirm that Docker and Compose are available:

```bash
docker version
docker compose version
```

Both commands must print version information without an error.

## 1. Create a Telegram bot

Open `@BotFather` in Telegram, run `/newbot`, and save the token it returns.

Send a message to the new bot. Query Telegram for that update, replacing the token in the URL:

```bash
curl -s "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates"
```

Find the positive integer at `result[].message.from.id`. This value is your allowed Telegram user ID.

## 2. Create the local configuration

Clone the repository and create the two local configuration files:

```bash
git clone https://github.com/maddhruv/rasptele.git
cd rasptele
cp .env.example .env
chmod 600 .env
cp config.example.yaml config.yaml
```

Edit `.env` with the values from the previous step. Keep the Pi-hole placeholder because Compose validates that the variable exists:

```dotenv
TELEGRAM_BOT_TOKEN=<TELEGRAM_BOT_TOKEN>
TELEGRAM_ALLOWED_USER_ID=<TELEGRAM_ALLOWED_USER_ID>
PIHOLE_PASSWORD=not-configured
```

Replace `config.yaml` with this minimal configuration:

```yaml
database_path: /data/rasptele.sqlite3
monitor_interval_seconds: 60
reminder_interval_minutes: 30
audit_retention_days: 90
alerts:
  disk_percent: 90
  temperature_celsius: 80
containers:
  restart_allowed: []
docker_guard_url: http://docker-guard:8080
integrations: {}
```

The `.env` and `config.yaml` files are ignored by Git. Do not commit either file.

## 3. Validate the Compose stack

Render the Compose configuration before starting it:

```bash
docker compose config --quiet
```

The command exits without output when the configuration is valid.

## 4. Start Rasptele

Build and start all three services:

```bash
docker compose up -d --build
docker compose ps
```

The service table reports `rasptele`, `docker-guard`, and `rasptele-watchdog` as running. None has a published host port.

Inspect the bot log if a service exits:

```bash
docker compose logs rasptele
```

## 5. Verify the bot

Open the private chat with your bot and send `/start`. The bot replies with the available commands.

Send `/status`. Rasptele returns CPU, memory, disk, temperature, throttling, and container-health information from the Raspberry Pi.

## What you've done

- Deployed Rasptele as three isolated Docker services.
- Restricted the bot to one Telegram account.
- Verified host and container monitoring without publishing an inbound port.

## Next steps

- [Allow selected container restarts or add Pi-hole](configuration.md)
- [Choose a managed deployment or update the stack](deployment.md)
- [Back up data and troubleshoot alerts](operations.md)
