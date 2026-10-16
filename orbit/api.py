"""REST API: /health, /status, /metrics, /worker, /history."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import __version__
from .config import Settings
from .docker_client import DockerBackend
from .store import Store, as_utc, utcnow
from .worker import Monitor


class ContainerOut(BaseModel):
    name: str
    container_id: str
    status: str
    health: str
    reason: str | None
    consecutive_failures: int
    restart_count: int
    last_restart_at: datetime | None
    last_checked_at: datetime


class EventOut(BaseModel):
    id: int
    container_name: str
    container_id: str
    created_at: datetime
    action: str
    reason: str
    outcome: str
    duration_seconds: float | None


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

    @app.get("/status", response_model=list[ContainerOut])
    def status():
        return [ContainerOut.model_validate(c, from_attributes=True) for c in store.list_states()]

    @app.get("/metrics")
    def metrics():
        states = store.list_states()
        events = store.list_events(limit=100_000)
        restarts = [e for e in events if e.action == "restart"]
        resolved = sum(1 for e in restarts if e.outcome == "resolved")
        by_health: dict[str, int] = {}
        for c in states:
            by_health[c.health] = by_health.get(c.health, 0) + 1
        return {
            "orbit_uptime_seconds": round((utcnow() - app.state.started_at).total_seconds()),
            "tracked_containers": len([c for c in states if c.status != "missing"]),
            "containers_by_health": by_health,
            "total_restarts": len(restarts),
            "successful_recoveries": resolved,
            "failed_recoveries": len(restarts) - resolved,
            "recovery_success_rate": round(resolved / len(restarts), 3) if restarts else None,
            "given_up": [c.name for c in states if c.health == "given_up"],
        }

    @app.get("/worker")
    def worker():
        st = monitor.status
        last = as_utc(st.last_poll_at)
        return {
            "running": st.running,
            "started_at": st.started_at,
            "poll_interval_seconds": settings.poll_interval,
            "poll_count": st.poll_count,
            "last_poll_at": last,
            "seconds_since_last_poll": round((utcnow() - last).total_seconds(), 1)
            if last
            else None,
            "last_poll_duration_seconds": st.last_poll_duration,
            "tracked_containers": st.tracked,
            "last_error": st.last_error,
        }

    @app.get("/history", response_model=list[EventOut])
    def history(
        container: str | None = Query(None, description="Filter by container name"),
        limit: int = Query(100, ge=1, le=1000),
    ):
        return [
            EventOut.model_validate(e, from_attributes=True)
            for e in store.list_events(container, limit)
        ]

    return app
