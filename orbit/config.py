"""Runtime configuration, read from environment variables (prefix ``ORBIT_``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # State store. SQLite for development, PostgreSQL for production, e.g.
    # postgresql+psycopg://orbit:orbit@db:5432/orbit
    database_url: str = "sqlite:///./orbit.db"

    # Only containers carrying this label (value "true") are tracked.
    label: str = "orbit.enable"

    # Seconds between monitoring passes.
    poll_interval: float = 10.0
    # Consecutive unhealthy checks required before Orbit acts (debounce).
    failure_threshold: int = 2
    # Minimum seconds between two restarts of the same container.
    cooldown_seconds: int = 60
    # At most this many restarts per container inside the rolling window...
    max_restarts: int = 3
    # ...of this many seconds. Past that, Orbit stops and flags the container.
    restart_window_seconds: int = 600
    # How long to wait for a restarted container to report healthy again.
    validation_timeout: float = 60.0
    # Timeout for each heartbeat HTTP request.
    heartbeat_timeout: float = 3.0
