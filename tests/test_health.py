from orbit.docker_client import ContainerInfo
from orbit.health import evaluate

ok = lambda url, t: None  # noqa: E731
bad = lambda url, t: "heartbeat failed: refused"  # noqa: E731


def info(status="running", health=None, labels=None):
    return ContainerInfo("id", "web", status, health, labels or {})


def test_running_without_healthcheck_is_healthy():
    assert evaluate(info(), ok).healthy is True


def test_exited_is_unhealthy():
    r = evaluate(info(status="exited"), ok)
    assert r.healthy is False and "exited" in r.reason


def test_docker_unhealthy():
    assert evaluate(info(health="unhealthy"), ok).healthy is False


def test_starting_has_no_verdict():
    assert evaluate(info(health="starting"), ok).healthy is None


def test_heartbeat_failure_catches_what_docker_misses():
    c = info(health="healthy", labels={"orbit.heartbeat.url": "http://web/ping"})
    assert evaluate(c, ok).healthy is True
    r = evaluate(c, bad)
    assert r.healthy is False and "heartbeat" in r.reason
