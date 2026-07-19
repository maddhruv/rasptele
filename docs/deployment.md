# Deploy and update Rasptele

Use these guides when moving Rasptele to a Raspberry Pi or updating an existing installation. Every deployment runs the same three services and publishes no inbound port.

## Prerequisites

- Docker on a 64-bit Raspberry Pi
- A BotFather token and numeric Telegram user ID
- A prepared `.env` and `config.yaml` as described in [the first deployment tutorial](getting-started.md)
- A reachable Pi-hole v6 instance and application password when the integration is enabled

## Deploy from a local checkout

Use `compose.yaml` when you manage the Raspberry Pi over Secure Shell (SSH) and build from source.

### 1. Start the stack

```bash
docker compose up -d --build
docker compose ps
```

### 2. Verify the deployment

Confirm that `rasptele`, `docker-guard`, and `rasptele-watchdog` remain running. Send `/start` and `/status` to the bot in a private chat. Send `/pihole` when the integration is configured.

### 3. Update the deployment

Pull the revision you intend to run and rebuild all services:

```bash
git pull --ff-only
docker compose up -d --build
docker compose ps
```

## Deploy with Coolify

Use `compose.coolify.yaml` to let Coolify build the image from a selected Git revision.

### 1. Create persistent configuration

Create the configuration file directly on the Raspberry Pi:

```bash
sudo install -d -m 700 /opt/rasptele
sudo install -m 600 /dev/null /opt/rasptele/config.yaml
sudo editor /opt/rasptele/config.yaml
```

Copy the relevant settings from `config.example.yaml`. Keep `docker_guard_url` set to `http://docker-guard:8080`.

### 2. Configure the resource

Create a **Docker Compose** resource with these values:

- Repository: `https://github.com/maddhruv/rasptele`
- Branch: `main` or a release tag
- Compose file: `/compose.coolify.yaml`
- Destination: the Raspberry Pi
- Runtime variables: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`, and `PIHOLE_PASSWORD`

Set `PIHOLE_PASSWORD=not-configured` when `integrations.pihole` is absent from `config.yaml`. Do not assign a domain or publish a port.

### 3. Verify the deployment

Confirm that all three services remain running. Open the `rasptele` logs in Coolify, then send `/start` and `/status` to the bot.

## Deploy with Portainer

Use `compose.portainer.yaml` to pull the released multi-architecture image from GitHub Container Registry (GHCR).

Before creating the stack, confirm that Portainer can pull `ghcr.io/maddhruv/rasptele`. Make the package public in its GitHub package settings, or add `ghcr.io` under **Registries** in Portainer with a GitHub personal access token that has `read:packages` permission.

### 1. Create persistent configuration

Create `/opt/rasptele/config.yaml` on the Docker host as described in the Coolify guide above.

### 2. Create the stack

In Portainer, open **Stacks**, select **Add stack**, then select **Repository**. Configure these values:

- Repository URL: `https://github.com/maddhruv/rasptele`
- Repository reference: `refs/heads/main`
- Compose path: `compose.portainer.yaml`

Add the required environment variables:

```dotenv
TELEGRAM_BOT_TOKEN=<TELEGRAM_BOT_TOKEN>
TELEGRAM_ALLOWED_USER_ID=<TELEGRAM_ALLOWED_USER_ID>
PIHOLE_PASSWORD=<PIHOLE_PASSWORD_OR_PLACEHOLDER>
```

Deploy the stack without publishing ports.

### 3. Verify the deployment

Confirm that all three services remain running. Inspect the `rasptele` logs, then send `/start` and `/status` to the bot.

### 4. Update the deployment

Update all three `image:` tags in `compose.portainer.yaml` to the same release. Pull and redeploy the stack from Portainer.

## Verify secret boundaries

Only `rasptele` receives `PIHOLE_PASSWORD`. The watchdog receives the Telegram credentials, while `docker-guard` receives none of these secrets.

Render any Compose file to inspect the resolved boundary:

```bash
docker compose -f compose.yaml config
docker compose -f compose.coolify.yaml config
docker compose -f compose.portainer.yaml config
```

See the [configuration reference](configuration.md) for every setting and secret.
