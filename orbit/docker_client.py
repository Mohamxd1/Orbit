"""Thin wrapper around the Docker SDK so the rest of Orbit is testable."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ContainerInfo:
    id: str
    name: str
    status: str  # running, exited, restarting, paused, dead, created
    health: str | None  # healthy, unhealthy, starting, or None if no HEALTHCHECK
    labels: dict[str, str] = field(default_factory=dict)


class DockerBackend(Protocol):
    def list_tracked(self, label: str) -> list[ContainerInfo]: ...
    def get(self, container_id: str) -> ContainerInfo | None: ...
    def restart(self, container_id: str, timeout: int = 10) -> None: ...
    def ping(self) -> bool: ...
