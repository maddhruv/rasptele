# Contributing to Rasptele

Rasptele welcomes focused bug fixes, documentation improvements, tests, and integrations that preserve its narrow security boundaries.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Set up a development environment

Rasptele requires Python 3.11 or newer. Clone the repository and create an isolated environment:

```bash
git clone https://github.com/maddhruv/rasptele.git
cd rasptele
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e '.[test]' ruff mypy
```

Run the unit tests to verify the environment:

```bash
python3 -m unittest discover -s tests -v
```

The test suite uses in-memory transports and temporary files. It does not require a Telegram token, Docker daemon, or Pi-hole server.

## Work on a change

Create a branch from the latest `main`:

```bash
git switch main
git pull --ff-only
git switch -c fix/short-description
```

Keep changes scoped to one problem. Add or update tests for observable behavior and update user documentation when configuration, commands, deployment, or alerts change.

Add user-visible changes to the appropriate `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, or `Security` subsection under `Unreleased` in `CHANGELOG.md`. Changelog entries should explain the effect for users, not the implementation details. Changes with no user-visible effect do not need an entry.

The main modules live in `src/rasptele/`:

| Module | Responsibility |
| --- | --- |
| `bot.py` | Telegram commands, callback controls, and authorization checks |
| `config.py` | Fail-closed environment validation |
| `guard.py` | Narrow Docker socket boundary and allowlisted restart API |
| `monitor.py` | Host, container, guard, and integration alert reconciliation |
| `pihole.py` | Authenticated Pi-hole v6 client and response validation |
| `store.py` | SQLite incidents, durable notifications, confirmations, and audit records |
| `watchdog.py` | Independent bot and guard failure detection |

Preserve these security invariants:

- Do not mount the Docker socket into the bot or watchdog.
- Do not add a generic Docker proxy route to `docker-guard`.
- Keep restart authority restricted to exact configured container names.
- Require the configured Telegram user and private chat for every command and callback.
- Keep destructive controls bound to single-use, expiring confirmations.
- Do not log tokens, application passwords, session IDs, or full secret-bearing responses.

Discuss changes that broaden host authority or introduce a new integration in an issue before implementation.

## Run the checks

Run the same static and unit checks as continuous integration (CI):

```bash
ruff check src tests
mypy --ignore-missing-imports src
python3 -m unittest discover -s tests -v
```

Build the image when changing dependencies, packaging, startup behavior, or Docker files:

```bash
docker build -t rasptele:local-test .
```

Validate Compose when changing configuration or deployment:

```bash
TELEGRAM_BOT_TOKEN=test-token \
TELEGRAM_ALLOWED_USER_ID=1 \
docker compose config --quiet
```

The [CI workflow](.github/workflows/ci.yml) is the final source of truth. It also rejects version drift between the release metadata, Python package, and Compose images.

## Submit a pull request

Push the branch and open a pull request against `main`. Complete the pull request template with:

- The user-visible problem and the chosen solution
- Security or deployment effects
- Tests and manual verification performed
- Documentation changes

Keep commits reviewable. The project does not enforce a commit-message convention or require issue assignment before a pull request.

Maintainers may decline features that expose an inbound management port, grant broad Docker access, weaken single-user authorization, or store secrets in tracked configuration.
