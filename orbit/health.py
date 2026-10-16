"""Health evaluation: Docker state + Docker HEALTHCHECK + optional HTTP heartbeat."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .docker_client import ContainerInfo

HEARTBEAT_LABEL = "orbit.heartbeat.url"

# Returns None if the heartbeat succeeded, or an error description.
HeartbeatFn = Callable[[str, float], "str | None"]


@dataclass
class HealthResult:
    # True = healthy, False = unhealthy, None = still starting (no verdict yet)
    healthy: bool | None
    reason: str


def evaluate(
    info: ContainerInfo, heartbeat: HeartbeatFn | None = None, timeout: float = 3.0
) -> HealthResult:
    if info.status != "running":
        return HealthResult(False, f"container is {info.status}")
    if info.health == "unhealthy":
        return HealthResult(False, "docker healthcheck reports unhealthy")
    if info.health == "starting":
        return HealthResult(None, "docker healthcheck is starting")

    return HealthResult(True, "ok")
