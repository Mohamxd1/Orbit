import pytest

from orbit.config import Settings
from orbit.docker_client import ContainerInfo
from orbit.recovery import RecoveryEngine
from orbit.store import Store
from orbit.worker import Monitor


class FakeDocker:
    """In-memory stand-in for the Docker daemon."""

    def __init__(self):
        self.containers: dict[str, ContainerInfo] = {}
        self.restarts: list[str] = []
        # What a container looks like after restart (default: healthy & running).
        self.after_restart: dict[str, ContainerInfo] = {}
        self.fail_restart = False

    def add(self, name, status="running", health=None, labels=None):
        info = ContainerInfo(
            id=f"id-{name}",
            name=name,
            status=status,
            health=health,
            labels={"orbit.enable": "true", **(labels or {})},
        )
        self.containers[info.id] = info
        return info

    def list_tracked(self, label):
        return [c for c in self.containers.values() if c.labels.get(label) == "true"]

    def get(self, container_id):
        return self.containers.get(container_id)

    def restart(self, container_id, timeout=10):
        if self.fail_restart:
            raise RuntimeError("daemon said no")
        self.restarts.append(container_id)
        old = self.containers[container_id]
        self.containers[container_id] = self.after_restart.get(
            container_id,
            ContainerInfo(old.id, old.name, "running", None, old.labels),
        )

    def ping(self):
        return True


@pytest.fixture
def settings():
    return Settings(
        database_url="sqlite://",
        failure_threshold=2,
        cooldown_seconds=60,
        max_restarts=3,
        restart_window_seconds=600,
        validation_timeout=0,
    )


@pytest.fixture
def docker():
    return FakeDocker()


@pytest.fixture
def store(settings):
    s = Store(settings.database_url)
    s.create_schema()
    return s


@pytest.fixture
def heartbeats():
    """Map of url -> error string (None = OK). Unknown urls succeed."""
    return {}


@pytest.fixture
def monitor(settings, docker, store, heartbeats):
    hb = lambda url, timeout: heartbeats.get(url)  # noqa: E731
    engine = RecoveryEngine(settings, docker, store, hb, sleep=lambda _: None)
    return Monitor(settings, docker, store, heartbeat=hb, recovery=engine)
