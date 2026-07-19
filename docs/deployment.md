# Deploy and update Rasptele

Every supported platform uses the repository's single `compose.yaml` and an exact released GHCR image. No configuration file, source build, domain, or published port is required.

## Deploy with Docker Compose

Clone an exact release and create `.env`:

```bash
git clone --branch v0.3.0 --depth 1 https://github.com/maddhruv/rasptele.git
cd rasptele
cp .env.example .env
```

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USER_ID`, then run:

```bash
docker compose up -d
docker compose ps
```

## Deploy with Portainer

Create a Git repository-backed stack:

- Repository: `https://github.com/maddhruv/rasptele`
- Repository reference: an exact tag such as `refs/tags/v0.3.0`
- Compose path: `compose.yaml`

Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USER_ID` in the stack environment-variable form. Add any optional variables from the [configuration reference](configuration.md), then deploy.

## Deploy with Coolify

Create a **Docker Compose** resource:

- Repository: `https://github.com/maddhruv/rasptele`
- Git reference: an exact tag such as `v0.3.0`
- Base directory: `/`
- Compose location: `/compose.yaml`

Add required and desired optional variables under **Environment Variables**. Leave domains, custom build/start commands, and pre/post-deployment commands empty. Disable automatic deployments so updates remain deliberate.

## Update deterministically

Wait for the desired GitHub Release to be published. For Docker Compose, fetch and switch to its exact tag, then pull and recreate:

```bash
git fetch --tags
git switch --detach <NEW_RELEASE_TAG>
docker compose pull
docker compose up -d
```

In Portainer or Coolify, change the Git reference to the new exact release tag, reload the Compose definition, and redeploy. All three services must show the same version.

An ordinary restart does not pull a newer image. `pull_policy: always` checks the selected tag during deploy/recreation, but the canonical exact tag never moves.

## Optional `latest` channel

Stable releases also publish `ghcr.io/maddhruv/rasptele:latest`. Users may replace all three exact image tags with `latest`, but updates still require pull plus recreation. Exact tags remain recommended because they identify the running version and make rollback deterministic.

## Roll back

Select a prior env-only release tag and redeploy. The persistent `rasptele-data` volume remains in place:

```bash
git switch --detach <PRIOR_ENV_ONLY_RELEASE_TAG>
docker compose pull
docker compose up -d
```

Releases from before the env-only migration used a required YAML file and different Compose manifests. Crossing that boundary is not a direct tag switch: restore the selected release's `config.example.yaml` as `config.yaml` and follow its matching deployment documentation. Prefer rolling back only between env-only releases.
