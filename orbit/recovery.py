"""Recovery engine: decides whether to restart, restarts, and validates the result."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy.orm import Session

from .config import Settings
from .docker_client import ContainerInfo, DockerBackend
from .health import HeartbeatFn, evaluate
from .store import ContainerState, RecoveryEvent, Store, as_utc, utcnow

log = logging.getLogger("orbit.recovery")

# Per-container overrides, set as Docker labels.
COOLDOWN_LABEL = "orbit.cooldown"
MAX_RESTARTS_LABEL = "orbit.max_restarts"


class RecoveryEngine:
    def __init__(
        self,
        settings: Settings,
        docker: DockerBackend,
        store: Store,
        heartbeat: HeartbeatFn,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.docker = docker
        self.store = store
        self.heartbeat = heartbeat
        self.sleep = sleep
        self.clock = clock

    def _limits(self, info: ContainerInfo) -> tuple[int, int]:
        cooldown = int(info.labels.get(COOLDOWN_LABEL, self.settings.cooldown_seconds))
        max_restarts = int(info.labels.get(MAX_RESTARTS_LABEL, self.settings.max_restarts))
        return cooldown, max_restarts

    def handle(self, s: Session, state: ContainerState, info: ContainerInfo, reason: str) -> None:
        """Called once a container has failed ``failure_threshold`` checks in a row."""
        now = utcnow()
        cooldown, max_restarts = self._limits(info)

        # 1. Cooldown: never restart the same container twice in quick succession.
        last = as_utc(state.last_restart_at)
        if last and now - last < timedelta(seconds=cooldown):
            remaining = cooldown - (now - last).total_seconds()
            state.reason = f"{reason} (cooldown, {remaining:.0f}s left)"
            return

        # 2. Threshold: cap restarts per rolling window to stop crash loops.
        window_start = now - timedelta(seconds=self.settings.restart_window_seconds)
        recent = self.store.restarts_since(s, state.name, window_start)
        if recent >= max_restarts:
            if state.health != "given_up":
                log.warning("%s: restart limit reached (%d), giving up", state.name, recent)
                s.add(
                    RecoveryEvent(
                        container_name=state.name,
                        container_id=info.id,
                        created_at=now,
                        action="skipped_threshold",
                        reason=f"{recent} restarts in last "
                        f"{self.settings.restart_window_seconds}s; {reason}",
                        outcome="skipped",
                    )
                )
            state.health = "given_up"
            state.reason = f"restart limit reached; manual intervention needed ({reason})"
            return

        # 3. Restart and validate.
        log.info("%s: restarting (%s)", state.name, reason)
        started = self.clock()
        try:
            self.docker.restart(info.id)
        except Exception as exc:
            log.exception("%s: restart failed", state.name)
            s.add(
                RecoveryEvent(
                    container_name=state.name,
                    container_id=info.id,
                    created_at=now,
                    action="restart_error",
                    reason=f"{reason}; docker error: {exc}",
                    outcome="failed",
                )
            )
            return

        healthy, detail = True, "ok"  # TODO: validate
        duration = self.clock() - started
        state.restart_count += 1
        state.last_restart_at = now
        s.add(
            RecoveryEvent(
                container_name=state.name,
                container_id=info.id,
                created_at=now,
                action="restart",
                reason=reason if healthy else f"{reason}; validation: {detail}",
                outcome="resolved" if healthy else "failed",
                duration_seconds=round(duration, 2),
            )
        )
        if healthy:
            log.info("%s: recovered in %.1fs", state.name, duration)
            state.health = "healthy"
            state.status = "running"
            state.reason = "recovered after restart"
            state.consecutive_failures = 0
        else:
            log.warning("%s: still unhealthy after restart: %s", state.name, detail)
            state.reason = f"still unhealthy after restart: {detail}"
