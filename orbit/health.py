"""Health evaluation: Docker state + Docker HEALTHCHECK + optional HTTP heartbeat."""

from __future__ import annotations

import urllib.error
import urllib.request
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


def http_heartbeat(url: str, timeout: float) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                return None
            return f"heartbeat returned HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return f"heartbeat returned HTTP {exc.code}"
    except Exception as exc:  # timeouts, connection refused, DNS...
        return f"heartbeat failed: {exc}"


def evaluate(
    info: ContainerInfo, heartbeat: HeartbeatFn = http_heartbeat, timeout: float = 3.0
) -> HealthResult:
    if info.status != "running":
        return HealthResult(False, f"container is {info.status}")
    if info.health == "unhealthy":
        return HealthResult(False, "docker healthcheck reports unhealthy")
    if info.health == "starting":
        return HealthResult(None, "docker healthcheck is starting")

    url = info.labels.get(HEARTBEAT_LABEL)
    if url:
        error = heartbeat(url, timeout)
        if error:
            return HealthResult(False, error)

    return HealthResult(True, "ok")
