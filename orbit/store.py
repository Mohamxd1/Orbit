"""Persistent state store: container state and recovery history (SQLAlchemy)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on read; treat naive timestamps as UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class Base(DeclarativeBase):
    pass


class ContainerState(Base):
    """Latest known state of one tracked container, keyed by container name."""

    __tablename__ = "container_state"

    name: Mapped[str] = mapped_column(String(255), primary_key=True)
    container_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))  # docker state: running, exited...
    health: Mapped[str] = mapped_column(String(32))  # healthy, unhealthy, starting, given_up
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    restart_count: Mapped[int] = mapped_column(Integer, default=0)
    last_restart_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RecoveryEvent(Base):
    """One decision the recovery engine made, and its outcome."""

    __tablename__ = "recovery_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    container_name: Mapped[str] = mapped_column(String(255), index=True)
    container_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # restart | skipped_cooldown | skipped_threshold | restart_error
    action: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    # resolved | failed | skipped
    outcome: Mapped[str] = mapped_column(String(32))
    duration_seconds: Mapped[float | None] = mapped_column(nullable=True)


class Store:
    def __init__(self, database_url: str) -> None:
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return self._sessions()

    # --- reads used by the API -------------------------------------------

    def list_states(self) -> list[ContainerState]:
        with self.session() as s:
            return list(s.scalars(select(ContainerState).order_by(ContainerState.name)))

    def list_events(self, container: str | None = None, limit: int = 100) -> list[RecoveryEvent]:
        with self.session() as s:
            q = select(RecoveryEvent).order_by(RecoveryEvent.id.desc()).limit(limit)
            if container:
                q = q.where(RecoveryEvent.container_name == container)
            return list(s.scalars(q))

    def restarts_since(self, s: Session, name: str, since: datetime) -> int:
        q = select(RecoveryEvent).where(
            RecoveryEvent.container_name == name,
            RecoveryEvent.action == "restart",
            RecoveryEvent.created_at >= since,
        )
        return len(list(s.scalars(q)))
