"""REST API: /health, /status, /metrics, /worker, /history."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import __version__
from .config import Settings
from .docker_client import DockerBackend
from .store import Store, utcnow
from .worker import Monitor


def create_app(
    settings: Settings | None = None,
    docker: DockerBackend | None = None,
    start_worker: bool = True,
) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.basicConfig(
        level=os.environ.get("ORBIT_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    store = Store(settings.database_url)
    store.create_schema()
    if docker is None:
        from .docker_client import DockerSDKBackend

        docker = DockerSDKBackend()
    monitor = Monitor(settings, docker, store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_worker:
            monitor.start()
        yield
        monitor.stop()

    app = FastAPI(title="Orbit", version=__version__, lifespan=lifespan)
    app.state.store = store
    app.state.monitor = monitor
    app.state.started_at = utcnow()

    @app.get("/health")
    def health():
        """Orbit's own liveness: API up, Docker reachable, worker alive."""
        docker_ok = docker.ping()
        worker_ok = monitor.status.running or not start_worker
        ok = docker_ok and worker_ok
        body = {"status": "ok" if ok else "degraded", "docker": docker_ok, "worker": worker_ok}
        return JSONResponse(body, status_code=200 if ok else 503)

    return app
