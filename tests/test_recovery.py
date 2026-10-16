from datetime import timedelta

from orbit.store import ContainerState, utcnow


def state(store, name):
    with store.session() as s:
        return s.get(ContainerState, name)


def test_healthy_container_is_tracked_not_restarted(monitor, docker, store):
    docker.add("web")
    monitor.poll_once()
    assert docker.restarts == []
    assert state(store, "web").health == "healthy"


def test_failure_threshold_debounces(monitor, docker, store):
    docker.add("web", status="exited")
    monitor.poll_once()
    assert docker.restarts == []  # 1 failure < threshold of 2
    assert state(store, "web").health == "unhealthy"
    monitor.poll_once()
    assert docker.restarts == ["id-web"]


def test_restart_is_validated_and_logged(monitor, docker, store):
    docker.add("web", status="exited")
    monitor.poll_once()
    monitor.poll_once()
    st = state(store, "web")
    assert st.health == "healthy" and st.restart_count == 1
    [event] = store.list_events("web")
    assert event.action == "restart" and event.outcome == "resolved"


def test_failed_validation_is_recorded(monitor, docker, store):
    c = docker.add("web", status="exited")
    docker.after_restart[c.id] = c  # stays exited after restart
    monitor.poll_once()
    monitor.poll_once()
    [event] = store.list_events("web")
    assert event.outcome == "failed"
    assert state(store, "web").health == "unhealthy"


def test_cooldown_blocks_immediate_second_restart(monitor, docker, store):
    c = docker.add("web", status="exited")
    docker.after_restart[c.id] = c
    for _ in range(5):
        monitor.poll_once()
    assert docker.restarts == ["id-web"]
    assert "cooldown" in state(store, "web").reason


def test_threshold_stops_crash_loop(monitor, docker, store, settings):
    c = docker.add("web", status="exited")
    docker.after_restart[c.id] = c
    for _ in range(10):
        monitor.poll_once()
        # Fast-forward past the cooldown by back-dating the last restart.
        with store.session() as s:
            st = s.get(ContainerState, "web")
            if st.last_restart_at:
                st.last_restart_at = utcnow() - timedelta(seconds=settings.cooldown_seconds + 1)
            s.commit()
    assert len(docker.restarts) == settings.max_restarts
    assert state(store, "web").health == "given_up"
    actions = [e.action for e in store.list_events("web")]
    assert actions.count("skipped_threshold") == 1  # logged once, not every poll


def test_per_container_label_overrides(monitor, docker, store):
    docker.add("web", status="exited", labels={"orbit.max_restarts": "0"})
    monitor.poll_once()
    monitor.poll_once()
    assert docker.restarts == []
    assert state(store, "web").health == "given_up"
