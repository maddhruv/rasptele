<p align="center">
  <img src="assets/rasptele-logo.png" alt="Rasptele logo: a white Raspberry Pi mark with wings on a blue circle" width="180">
</p>

# Rasptele

Rasptele is a private Telegram control plane for a Raspberry Pi Docker server. It lets one trusted Telegram account monitor host and container health, receive alerts, and restart explicitly approved containers—without exposing an inbound port.

## What it does today

Rasptele v1 provides:

- Outbound-only Telegram long polling; the supplied Compose stack publishes no ports.
- Single-user access control using a numeric Telegram user ID, restricted to that user's private chat.
- `/status` for host CPU, RAM, disk, temperature, throttling, and container-health summary.
- `/containers` for every Docker container, with a restart control only for configured names.
- Stateful alerts for disk usage, CPU temperature, throttling, stopped containers, unhealthy containers, and Docker guard outages.
- Durable alert delivery: unsent Telegram notifications remain in a SQLite outbox for retry.
- An independent watchdog that can report failure of the main bot or Docker guard.
- A confirmation button that is bound to your Telegram ID, expires after 60 seconds, and can be used once.
- `/audit` for the latest local incident and action records.

Pi-hole, qBittorrent, Jellyfin, Coolify, and OpenWrt integrations are not implemented yet.

## Quickstart

These steps target a Raspberry Pi 5 running a 64-bit Linux distribution with Docker Engine and the Docker Compose plugin.

### 1. Clone the repository

```sh
git clone https://github.com/maddhruv/rasptele.git
cd rasptele
```

### 2. Create a Telegram bot and find your user ID

1. In Telegram, open `@BotFather`, run `/newbot`, and save the token it gives you.
2. Send a message to your new bot. It will not reply until Rasptele is deployed, but Telegram will record the update.
3. Create the secret file:

   ```sh
   cp .env.example .env
   chmod 600 .env
   ```

4. To get your numeric Telegram user ID, load the token into the current shell and query your bot's updates:

   ```sh
   set -a
   . ./.env
   set +a
   curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates"
   ```

   Find `message.from.id` in the JSON response. Add the token and that numeric ID to `.env`:

   ```dotenv
   TELEGRAM_BOT_TOKEN=123456789:replace-with-your-token
   TELEGRAM_ALLOWED_USER_ID=123456789
   ```

Keep `.env` private. It is ignored by Git and Docker build contexts and must never be committed.

### 3. Configure Rasptele

```sh
cp config.example.yaml config.yaml
```

Edit `config.yaml`. The most important setting is the restart allowlist. Rasptele shows every container, but only names in this list receive a restart button.

```yaml
containers:
  restart_allowed:
    - pihole
    - jellyfin
```

Use exact Docker container names. Check them before deployment:

```sh
docker ps --format '{{.Names}}'
```

### 4. Deploy

```sh
docker compose up -d --build
docker compose ps
docker compose logs -f rasptele
```

All three services should remain running. Send `/start` to the bot in a private chat, then run `/status`.

To update a source checkout later, pull the revision you intend to deploy and rebuild:

```sh
git pull
docker compose up -d --build
```

## Deploy with Coolify

Deploy the Git repository as a **Docker Compose** resource on the Raspberry Pi that Rasptele will manage. Use `compose.coolify.yaml`; its `build: .` entries make Coolify build the image from the selected Git revision. The `image:` values name the resulting local images and do not require GitHub Container Registry (GHCR) access.

This is the recommended deployment path because Rasptele requires three services, host mounts, the Docker socket, runtime secrets, and persistent storage. A standalone container image does not describe those resources.

Before deploying, commit and push `compose.coolify.yaml` and the revision you intend Coolify to run. Connect the repository through the Coolify GitHub App and enable automatic deployments if pushes to `main` should redeploy Rasptele.

### Create the persistent configuration

Create the configuration directly on the Pi:

```sh
sudo install -d -m 700 /opt/rasptele
sudo install -m 600 /dev/null /opt/rasptele/config.yaml
sudo editor /opt/rasptele/config.yaml
```

Copy the non-secret settings from `config.example.yaml` into that file and adjust the container restart allowlist.

### Configure the Coolify resource

Create the resource with these values:

- Repository: this GitHub repository.
- Branch: `main`.
- Compose file: `/compose.coolify.yaml`.
- Destination server: the Raspberry Pi.
- Runtime variables: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USER_ID`.

Keep both Telegram values runtime-only. Do not assign a domain or publish a port for any service. Deploy only on a dedicated, trusted Pi because the stack reads host metrics and `docker-guard` controls allowlisted containers through the Docker socket.

The Coolify Compose file keeps `/var/run/docker.sock` exclusive to `docker-guard`, mounts host metrics read-only into `rasptele`, and persists SQLite data in `rasptele-data`. Keep these service boundaries intact.

After deployment, confirm that `rasptele`, `docker-guard`, and `rasptele-watchdog` remain running. Open the `rasptele` logs in Coolify, then send `/start` and `/status` to the bot in a private Telegram chat.

### Publish optional GHCR images

GHCR images are optional for the recommended source deployment. GitHub Actions publishes ARM64 and AMD64 images only when a version tag matching `v*` is pushed:

```sh
git tag v0.1.0
git push origin main v0.1.0
```

For `v0.1.0`, the workflow publishes these tags:

```text
ghcr.io/maddhruv/rasptele:0.1.0
ghcr.io/maddhruv/rasptele:0.1
ghcr.io/maddhruv/rasptele:v0.1.0
```

An image-only deployment still needs a Compose definition with the same three services, mounts, secrets, and volume. It must also authenticate to GHCR when the package is private.

## Use Rasptele from Telegram

| Command | Result |
| --- | --- |
| `/start` or `/help` | Confirms the bot is online and lists the available commands. |
| `/status` | Shows CPU, RAM, disk, temperature, throttling, and container-health summary. |
| `/containers` | Opens the container picker. Select a container for its state, health, image, and restart count. |
| `/audit` | Shows the ten most recent local audit records. |

For an allowlisted container, select **Restart**, then select **Confirm restart** within 60 seconds. A confirmation is valid only for your configured user ID and cannot be reused. A container outside `restart_allowed` has no restart control.

Commands outside the configured user's private chat are ignored. The attempt is recorded locally without a reply.

## Configuration reference

`config.yaml` is non-secret and is mounted read-only into all three services.

| Key | Default | Description |
| --- | --- | --- |
| `database_path` | `/data/rasptele.sqlite3` | SQLite path for alerts, confirmations, and audit data. |
| `monitor_interval_seconds` | `60` | Seconds between full host and Docker reconciliation checks. Must be positive. |
| `reminder_interval_minutes` | `30` | Minutes between reminders for an active alert. Must be positive. |
| `audit_retention_days` | `90` | Days to retain audit records and resolved incidents. Must be positive. |
| `alerts.disk_percent` | `90` | Alert when host disk usage reaches this percentage. Must be greater than 0 and at most 100. |
| `alerts.temperature_celsius` | `80` | Alert when the Pi thermal-zone temperature reaches this value. Must be positive. |
| `containers.restart_allowed` | `[]` | Exact Docker container names that may be restarted after confirmation. |
| `docker_guard_url` | `http://docker-guard:8080` | Internal Docker guard URL. Do not expose it publicly. |

The bot reads these required values from `.env`:

| Variable | Description |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Token created with BotFather. |
| `TELEGRAM_ALLOWED_USER_ID` | Positive numeric Telegram user ID permitted to use the bot. |

Rasptele fails closed and does not begin Telegram polling if a required secret is missing, the user ID is not numeric, or the YAML configuration is invalid.

## Alerts and monitoring

Rasptele subscribes to Docker container events and also reconciles state on the configured interval. It opens an alert on the first failing observation, sends reminders while it remains active, and sends a recovery message when it clears. Notifications are acknowledged only after Telegram accepts them; failures remain in the durable outbox for retry.

The current alert conditions are:

- Host disk usage at or above `alerts.disk_percent`.
- CPU temperature at or above `alerts.temperature_celsius` when the Pi thermal-zone sensor is available.
- A Raspberry Pi throttling or under-voltage signal when the firmware sysfs value is available.
- A container that is not running or reports Docker health status `unhealthy`.
- An unavailable Docker guard.
- The main bot container being down, reported independently by `rasptele-watchdog`.

The host temperature and throttling fields appear as `unavailable` when the host does not expose the expected Raspberry Pi sysfs paths.

## Operate and troubleshoot

### Inspect services and logs

```sh
docker compose ps
docker compose logs -f rasptele
docker compose logs -f docker-guard
```

### Validate configuration without starting the stack

The bot exits with a `configuration error` when its secret or YAML validation fails. Confirm the files exist and contain the required values:

```sh
test -f .env && test -f config.yaml && echo "configuration files found"
docker compose config
```

### Back up audit and alert history

The named `rasptele-data` volume contains the SQLite database. Stop the stack before taking a consistent volume backup:

```sh
docker compose down
docker run --rm -v rasptele_rasptele-data:/data -v "$PWD:/backup" alpine \
  tar czf /backup/rasptele-data-backup.tgz -C /data .
docker compose up -d
```

The volume name above is Docker Compose's default for this repository. Confirm it first with `docker volume ls` if you changed the Compose project name.

### Common problems

| Symptom | Check |
| --- | --- |
| Bot does not reply | Verify the token and user ID, then inspect `docker compose logs rasptele`. The bot ignores any user ID other than `TELEGRAM_ALLOWED_USER_ID`. |
| `Docker guard is unavailable` alert | Confirm `docker-guard` is running and has access to `/var/run/docker.sock`. Inspect its logs. |
| No temperature or throttling reading | Confirm this is a supported Raspberry Pi Linux host and that the read-only `/sys` mount remains in the Compose file. |
| No restart button | Confirm the exact Docker container name is in `containers.restart_allowed`, then redeploy after editing `config.yaml`. |

## Security model

Rasptele is designed to keep the bot's authority narrow, but it still operates close to the host. Review the deployment before using it on a server with sensitive workloads.

- The `rasptele` bot has no Docker socket mount. `docker-guard` is the only service with that socket.
- The guard exposes only sanitized container status, sanitized container lifecycle events, and restart for names in the configured allowlist. It does not expose generic Docker API routes.
- The bot requires a configured Telegram user ID and private chat and silently rejects every other context.
- Every v1 restart requires a second, single-use confirmation that expires after 60 seconds.
- Rasptele mounts host `/proc`, `/sys`, and `/` read-only to collect real host metrics. Read-only access to `/` is still sensitive; do not broaden these mounts or expose the stack to an untrusted network.
- The stack defines no inbound ports. Telegram communication uses outbound long polling; the watchdog only sends outbound messages.

See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## Project layout

```text
src/rasptele/       Bot, monitoring, watchdog, configuration, SQLite store, and Docker guard
compose.yaml        Three-service local production deployment
compose.coolify.yaml Coolify deployment using persistent host configuration
config.example.yaml Reviewable non-secret configuration template
.env.example        Secret environment-variable template
tests/              Configuration, incident, and confirmation tests
```

## Development and verification

Rasptele requires Python 3.11 or newer for local development.

```sh
python -m pip install . ruff mypy
ruff check src tests
mypy --ignore-missing-imports src
python -m unittest discover -s tests -v
docker build -t rasptele:local-test .
```

GitHub Actions runs linting, type checks, tests, a container build, and an image vulnerability scan. Tagged releases publish ARM64 and AMD64 images to GitHub Container Registry.

## License

Rasptele is licensed under the [Apache License 2.0](LICENSE).
