# Security policy

Do not open a public port for Rasptele. Keep the Telegram bot token, allowed user ID, service tokens, and local configuration out of version control.

Rasptele is intentionally single-user. Report suspected vulnerabilities privately to the repository owner rather than opening a public issue; include reproduction steps and impact. Do not include real secrets in reports.

The Docker guard holds the Docker socket and is security-sensitive. Review changes to its routes and the host read-only mounts before deploying upgrades.
