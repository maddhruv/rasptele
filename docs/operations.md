# Operate and troubleshoot Rasptele

## Inspect services and configuration

```bash
docker compose ps
docker compose logs -f rasptele
docker compose config --quiet
```

Compose validates interpolation; each process validates its environment at startup. Inspect `docker compose logs rasptele` for a variable-specific `configuration error`.

## Back up data

The `rasptele-data` volume contains SQLite alerts, confirmations, and audit history. Stop the stack for a consistent archive:

```bash
docker compose down
docker run --rm -v rasptele_rasptele-data:/data -v "$PWD:/backup" \
  alpine tar czf /backup/rasptele-data-backup.tgz -C /data .
docker compose up -d
```

Confirm the actual volume name with `docker volume ls` if the Compose project name differs.

## Test Pi-hole recovery alerts

Temporarily set `PIHOLE_URL=http://192.0.2.1` with a non-empty `PIHOLE_PASSWORD`, then recreate only the bot:

```bash
docker compose up -d --force-recreate rasptele
```

After one monitoring interval plus timeout, Telegram reports the outage. Restore the real URL and recreate `rasptele` again; the next check reports recovery.

## Common failures

| Symptom | Resolution |
| --- | --- |
| Bot does not reply | Verify Telegram variables and inspect bot logs. Only the configured user in a private chat is accepted. |
| Docker guard unavailable | Confirm `docker-guard` is running and retains its Docker socket mount. |
| Pi-hole unavailable | Verify `PIHOLE_URL`, application password, Pi-hole v6, and network reachability. |
| `/pihole` not configured | Set both Pi-hole variables and recreate `rasptele`. |
| No restart button | Add the exact name to `RASPTELE_RESTART_ALLOWED`, then recreate `rasptele` and `docker-guard`. |
| Temperature unavailable | Confirm supported Raspberry Pi Linux host and unchanged `/sys` mount. |
