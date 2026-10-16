"""Monitor loop: polls tracked containers and hands failures to the recovery engine."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from .config import Settings
from .docker_client import DockerBackend
from .health import HeartbeatFn, evaluate, http_heartbeat
from .recovery import RecoveryEngine
from .store import ContainerState, Store, utcnow

log = logging.getLogger("orbit.worker")


@dataclass
class WorkerStatus:
    running: bool = False
    started_at: datetime | None = None
    last_poll_at: datetime | None = None
    last_poll_duration: float | None = None
    poll_count: int = 0
    last_error: str | None = None
    tracked: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class Monitor:
    def __init__(
        self,
        settings: Settings,
        docker: DockerBackend,
        store: Store,
        heartbeat: HeartbeatFn = http_heartbeat,
        recovery: RecoveryEngine | None = None,
    ) -> None:
        self.settings = settings
        self.docker = docker
        self.store = store
        self.heartbeat = heartbeat
        self.recovery = recovery or RecoveryEngine(settings, docker, store, heartbeat)
        self.status = WorkerStatus()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        self.status.running = True
        self.status.started_at = utcnow()
        self._thread = threading.Thread(target=self._loop, name="orbit-monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
        self.status.running = False

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
                self.status.last_error = None
            except Exception as exc:  # keep the loop alive no matter what
                log.exception("monitor pass failed")
                self.status.last_error = str(exc)
            self._stop.wait(self.settings.poll_interval)

    # --- one monitoring pass ----------------------------------------------

    def poll_once(self) -> None:
        started = time.monotonic()
        containers = self.docker.list_tracked(self.settings.label)
        seen: set[str] = set()

        with self.store.session() as s:
            for info in containers:
                seen.add(info.name)
                now = utcnow()
                state = s.get(ContainerState, info.name)
                if state is None:
                    state = ContainerState(
                        name=info.name,
                        container_id=info.id,
                        status=info.status,
                        health="unknown",
                        consecutive_failures=0,
                        restart_count=0,
                        last_checked_at=now,
                        first_seen_at=now,
                    )
                    s.add(state)

                result = evaluate(info, self.heartbeat, self.settings.heartbeat_timeout)
                state.container_id = info.id
                state.status = info.status
                state.last_checked_at = now

                if result.healthy is True:
                    state.health = "healthy"
                    state.reason = None
                    state.consecutive_failures = 0
                elif result.healthy is None:
                    state.health = "starting"
                    state.reason = result.reason
                else:
                    state.consecutive_failures += 1
                    if state.health != "given_up":
                        state.health = "unhealthy"
                    state.reason = result.reason
                    if state.consecutive_failures >= self.settings.failure_threshold:
                        self.recovery.handle(s, state, info, result.reason)

                s.commit()

            s.commit()

        with self.status.lock:
            self.status.poll_count += 1
            self.status.last_poll_at = utcnow()
            self.status.last_poll_duration = round(time.monotonic() - started, 3)
            self.status.tracked = len(containers)
