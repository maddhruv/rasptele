# Operate and troubleshoot Rasptele

Use these procedures to inspect a running stack, preserve its data, and diagnose common failures.

## Inspect services and logs

List the three services and follow their logs:

```bash
docker compose ps
docker compose logs -f rasptele
docker compose logs -f docker-guard
docker compose logs -f rasptele-watchdog
```

All services must remain running. A clean bot startup begins Telegram polling after configuration validation.

## Validate configuration

Confirm that the local files exist and render the Compose model:

```bash
test -f .env && test -f config.yaml && echo "configuration files found"
docker compose config --quiet
```

The second command exits without output when interpolation and Compose validation succeed. Application-level validation runs when each service starts.

## Back up audit and alert history

The `rasptele-data` volume contains the SQLite database. Stopping the stack makes the archive consistent but temporarily stops monitoring.

Confirm the volume name before the backup if you changed the Compose project name:

```bash
docker volume ls
```

Stop the stack, archive the volume into the current directory, and restart it:

```bash
docker compose down
docker run --rm \
  -v rasptele_rasptele-data:/data \
  -v "$PWD:/backup" \
  alpine tar czf /backup/rasptele-data-backup.tgz -C /data .
docker compose up -d
```

Verify that `rasptele-data-backup.tgz` exists before moving or deleting the source volume.

## Test Pi-hole outage and recovery alerts

Use the documentation-only TEST-NET-1 address to test alerts without stopping DNS for the network.

1. Back up the deployed `config.yaml`.
2. Set `integrations.pihole.url` to `http://192.0.2.1`.
3. Restart only `rasptele`:

   ```bash
   docker compose restart rasptele
   ```

4. Wait for one monitoring interval plus the ten-second API timeout. Telegram reports `⚠️ Alert: Pi-hole service is unavailable`.
5. Restore `config.yaml` and restart `rasptele` again.
6. Wait for the next check. Telegram reports `✅ Recovered: Pi-hole service restored`.

Restart the `rasptele` service from Coolify or Portainer when using either platform. Do not recreate the data volume or the other services.

## Resolve common failures

| Symptom | Resolution |
| --- | --- |
| Bot does not reply | Verify the token and user ID, then inspect `docker compose logs rasptele`. The bot ignores every other user ID and non-private chat. |
| `Docker guard is unavailable` | Confirm that `docker-guard` is running and has access to `/var/run/docker.sock`. Inspect its logs. |
| `Pi-hole service is unavailable` | Confirm that the URL is reachable from the Docker host, the server runs Pi-hole v6, and `PIHOLE_PASSWORD` contains a current application password. |
| `/pihole` says the integration is not configured | Add `integrations.pihole.url`, provide `PIHOLE_PASSWORD`, and restart `rasptele`. |
| Temperature or throttling is unavailable | Confirm that the host is a supported Raspberry Pi Linux system and the read-only `/sys` mount remains present. |
| A container has no restart button | Add its exact Docker name to `containers.restart_allowed`, then restart the services that read `config.yaml`. |

See the [configuration reference](configuration.md) for defaults and validation constraints.
