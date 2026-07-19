"""Narrow Docker API guard. This is the only Rasptele component with the socket."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import docker
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .config import Config, ConfigurationError, load_config


class GuardState:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = docker.DockerClient(base_url="unix:///var/run/docker.sock")

    def containers(self) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for container in self.client.containers.list(all=True):
            attrs = container.attrs
            state = attrs.get("State", {})
            name = container.name
            result.append(
                {
                    "id": container.short_id,
                    "name": name,
                    "image": (attrs.get("Config", {}).get("Image") or "unknown"),
                    "status": state.get("Status", "unknown"),
                    "health": (state.get("Health") or {}).get("Status"),
                    "started_at": state.get("StartedAt"),
                    "restart_count": attrs.get("RestartCount", 0),
                    "restart_allowed": name in self.config.restart_allowed,
                    "compose_service": (attrs.get("Config", {}).get("Labels") or {}).get(
                        "com.docker.compose.service"
                    ),
                }
            )
        return sorted(result, key=lambda item: str(item["name"]))

    def restart(self, name: str) -> None:
        if name not in self.config.restart_allowed:
            raise HTTPException(status_code=403, detail="container restart is not permitted")
        try:
            self.client.containers.get(name).restart()
        except docker.errors.NotFound as exc:
            raise HTTPException(status_code=404, detail="container not found") from exc
        except docker.errors.APIError as exc:
            raise HTTPException(status_code=502, detail="Docker refused restart") from exc


class RestartRequest(BaseModel):
    name: str


def create_app(config: Config) -> FastAPI:
    state = GuardState(config)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            state.client.ping()
        except docker.errors.DockerException as exc:
            raise RuntimeError("cannot connect to Docker socket") from exc
        yield
        state.client.close()

    app = FastAPI(title="Rasptele Docker Guard", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.get("/healthz")
    def health() -> dict[str, bool]:
        state.client.ping()
        return {"ok": True}

    @app.get("/v1/containers")
    def containers() -> list[dict[str, object]]:
        return state.containers()

    @app.get("/v1/events")
    def events() -> StreamingResponse:
        def stream():
            for event in state.client.events(decode=True, filters={"type": "container"}):
                actor = event.get("Actor") or {}
                attributes = actor.get("Attributes") or {}
                safe_event = {
                    "action": event.get("Action"),
                    "name": attributes.get("name"),
                    "compose_service": attributes.get("com.docker.compose.service"),
                    "time": event.get("time"),
                }
                yield json.dumps(safe_event) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.post("/v1/restart")
    def restart(request: RestartRequest) -> dict[str, str]:
        state.restart(request.name)
        return {"status": "restarted", "name": request.name}

    return app


def main() -> None:
    try:
        config = load_config(require_telegram=False, load_pihole=False)
    except ConfigurationError as exc:
        raise SystemExit(f"configuration error: {exc}") from exc
    import uvicorn

    try:
        app = create_app(config)
    except docker.errors.DockerException as exc:
        raise SystemExit(f"Docker guard startup error: {exc}") from exc
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
