# Security policy

Rasptele controls services on a Docker host, so security reports receive priority.

## Supported versions

| Version | Security updates |
| --- | --- |
| Latest release | Supported |
| Older releases | Not supported |

Update to the latest release before reporting a vulnerability that may already be fixed.

## Report a vulnerability privately

Do not open a public issue for a suspected vulnerability.

Use [GitHub private vulnerability reporting](https://github.com/maddhruv/rasptele/security/advisories/new). Include:

- The affected version or commit
- Deployment details relevant to the issue
- Reproduction steps or a minimal proof of concept
- Expected and observed behavior
- The security impact and any known mitigations

Remove real Telegram tokens, user IDs, Pi-hole passwords, session IDs, host data, and other secrets from the report.

The maintainer will acknowledge a complete report as soon as practical, investigate it privately, and coordinate remediation and disclosure with the reporter. Complex reports may require follow-up questions before impact can be confirmed.

## Security boundaries

The supported deployment assumes a dedicated, trusted Raspberry Pi and outbound Telegram access. It publishes no ports.

- `rasptele` reads host metrics through read-only mounts and has no Docker socket.
- `docker-guard` owns the Docker socket and exposes only container status, lifecycle events, and allowlisted restarts inside the Compose network.
- `rasptele-watchdog` independently reports bot and guard failures.
- Telegram commands require the configured numeric user ID in that user's private chat.
- Container restarts and temporary Pi-hole blocking disablement use expiring, single-use confirmations.

Reports about weakening or bypassing these boundaries are in scope. General hardening suggestions without a demonstrated security impact belong in a regular issue.
