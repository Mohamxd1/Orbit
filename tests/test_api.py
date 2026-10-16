from fastapi.testclient import TestClient

from orbit.api import create_app


def make_client(settings, docker):
    app = create_app(settings, docker, start_worker=False)
    return app, TestClient(app)


def test_endpoints(settings, docker):
    app, client = make_client(settings, docker)
    docker.add("web")
    docker.add("db", status="exited")
    monitor = app.state.monitor
    monitor.recovery.sleep = lambda _: None
    monitor.poll_once()
    monitor.poll_once()

    assert client.get("/health").json()["status"] == "ok"

    status = {c["name"]: c for c in client.get("/status").json()}
    assert status["web"]["health"] == "healthy"
    assert status["db"]["restart_count"] == 1

    m = client.get("/metrics").json()
    assert m["tracked_containers"] == 2
    assert m["total_restarts"] == 1 and m["recovery_success_rate"] == 1.0

    w = client.get("/worker").json()
    assert w["poll_count"] == 2

    assert len(client.get("/history").json()) == 1
    assert client.get("/history", params={"container": "web"}).json() == []


def test_health_degraded_when_docker_unreachable(settings, docker):
    docker.ping = lambda: False
    _, client = make_client(settings, docker)
    r = client.get("/health")
    assert r.status_code == 503 and r.json()["docker"] is False
