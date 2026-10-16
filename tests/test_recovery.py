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
