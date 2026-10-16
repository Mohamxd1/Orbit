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


class DockerSDKBackend:
    """Talks to the local Docker daemon (via /var/run/docker.sock or DOCKER_HOST)."""

    def __init__(self) -> None:
        import docker

        self._client = docker.from_env()

    @staticmethod
    def _info(container) -> ContainerInfo:
        state = container.attrs.get("State", {})
        health = (state.get("Health") or {}).get("Status")
        return ContainerInfo(
            id=container.id,
            name=container.name,
            status=state.get("Status", container.status),
            health=health,
            labels=container.labels or {},
        )

    def list_tracked(self, label: str) -> list[ContainerInfo]:
        containers = self._client.containers.list(all=True, filters={"label": f"{label}=true"})
        return [self._info(c) for c in containers]

    def get(self, container_id: str) -> ContainerInfo | None:
        import docker.errors

        try:
            return self._info(self._client.containers.get(container_id))
        except docker.errors.NotFound:
            return None

    def restart(self, container_id: str, timeout: int = 10) -> None:
        self._client.containers.get(container_id).restart(timeout=timeout)

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:
            return False
