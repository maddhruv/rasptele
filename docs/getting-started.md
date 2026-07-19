# Deploy your first Rasptele bot

This tutorial deploys the released Rasptele image on a 64-bit Raspberry Pi. You need Docker Engine with Compose, outbound access to Telegram, and a Telegram account.

## 1. Create Telegram credentials

Open `@BotFather`, run `/newbot`, and save the token. Send a message to the new bot, then find your positive numeric user ID:

```bash
curl -s "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates"
```

Read `result[].message.from.id` from the response.

## 2. Configure Rasptele

Use an exact release tag so both the Compose definition and image version are immutable. Release-it keeps this example synchronized with the release containing these instructions:

```bash
git clone --branch v0.3.0 --depth 1 https://github.com/maddhruv/rasptele.git
cd rasptele
cp .env.example .env
chmod 600 .env
```

Set these two values in `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=<TELEGRAM_BOT_TOKEN>
TELEGRAM_ALLOWED_USER_ID=<TELEGRAM_ALLOWED_USER_ID>
```

All other variables have defaults. No host configuration file is required.

## 3. Start and verify

```bash
docker compose config --quiet
docker compose up -d
docker compose ps
```

The stack pulls one released image and starts `rasptele`, `docker-guard`, and `rasptele-watchdog` without publishing host ports. Send `/start` and `/status` to the bot in a private Telegram chat.

## Next steps

- [Allow container restarts or configure Pi-hole](configuration.md)
- [Deploy through Portainer or Coolify and update safely](deployment.md)
- [Back up data and troubleshoot alerts](operations.md)
