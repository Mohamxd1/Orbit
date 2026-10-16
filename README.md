# Orbit

**Self-healing container orchestration for plain Docker hosts.**

Orbit watches your Docker containers. When one crashes, fails its health
check or stops answering its heartbeat, Orbit restarts it, then confirms it
actually came back healthy. Cooldowns and restart limits keep a broken
container from being restarted forever, and every decision is written to a
database. A REST API shows live status, metrics and recovery history.

It is meant for a single host running Docker or Docker Compose, where
Kubernetes would be overkill.

---

## Contents

1. [How it works](#how-it-works)
2. [Architecture](#architecture)
3. [Project layout](#project-layout)
4. [Quick start (deploy in 5 steps)](#quick-start-deploy-in-5-steps)
5. [Putting your own containers under Orbit](#putting-your-own-containers-under-orbit)
6. [Configuration](#configuration)
7. [API reference](#api-reference)
8. [Local development](#local-development)
9. [How the project was built (step by step)](#how-the-project-was-built-step-by-step)
10. [Production checklist](#production-checklist)
11. [Known limitations and roadmap](#known-limitations-and-roadmap)

---

## How it works

Every `ORBIT_POLL_INTERVAL` seconds the monitor runs one pass:

```
for each container labelled orbit.enable=true:
    1. CHECK     is it running?  docker HEALTHCHECK not "unhealthy"?  heartbeat URL returns 2xx?
    2. DEBOUNCE  unhealthy N times in a row (ORBIT_FAILURE_THRESHOLD)?        no  -> wait
    3. COOLDOWN  restarted less than ORBIT_COOLDOWN_SECONDS ago?              yes -> wait
    4. LIMIT     >= ORBIT_MAX_RESTARTS restarts in ORBIT_RESTART_WINDOW_SECONDS? yes -> give up, flag it
    5. RESTART   docker restart <container>
    6. VALIDATE  healthy again and stays healthy for ORBIT_VALIDATION_STABLE_SECONDS?
                    yes -> event "resolved"     no (by ORBIT_VALIDATION_TIMEOUT) -> event "failed"
    7. RECORD    container state + recovery event written to the database
```

Health is checked in three layers. Each layer catches failures the ones
before it miss:

| Layer | Source | Catches |
|---|---|---|
| Docker state | `State.Status` | Crashed or exited processes |
| Docker health check | `HEALTHCHECK` in the image or Compose file | App-defined failures |
| Heartbeat | `orbit.heartbeat.url` label, HTTP GET, must return 2xx | Hung processes that are still "running", broken networking |

A container that gave up (`health: given_up`) is not restarted again until
its old restarts fall out of the rolling window, or until it turns healthy
on its own, for example after you fix it by hand.

## Architecture

```
                    ┌──────────────────────────── Orbit container ───────────────────────────┐
                    │                                                                         │
 HTTP clients ─────▶│  REST API (FastAPI)          Monitor worker (background thread)         │
 dashboards,        │  /health /status /metrics    every poll_interval:                       │
 curl, Grafana      │  /worker /history              list labelled containers ───────────────┼──▶ Docker daemon
                    │        │                       evaluate health ──── heartbeat HTTP ─────┼──▶ app containers
                    │        │                       RecoveryEngine:                          │     (orbit network)
                    │        │                         cooldown → limit → restart → validate ─┼──▶ Docker daemon
                    │        ▼                              │                                  │
                    │   Store (SQLAlchemy) ◀────────────────┘                                  │
                    └────────┬────────────────────────────────────────────────────────────────┘
                             ▼
                  PostgreSQL (prod) / SQLite (dev)
                  tables: container_state, recovery_event
```

| Component | File | Job |
|---|---|---|
| Config | `orbit/config.py` | Reads all `ORBIT_*` environment variables into one `Settings` object |
| Docker client | `orbit/docker_client.py` | Thin wrapper around the Docker SDK (list, inspect, restart, ping). A protocol so tests can swap in a fake |
| Health | `orbit/health.py` | Turns one container snapshot into a verdict: healthy, unhealthy, or starting |
| Recovery engine | `orbit/recovery.py` | Cooldown, restart limit, restart, validation, audit event |
| Monitor worker | `orbit/worker.py` | The polling loop. Updates state, counts consecutive failures, calls the recovery engine |
| Store | `orbit/store.py` | Database models and queries (`container_state`, `recovery_event`) |
| API | `orbit/api.py` | FastAPI app. Starts and stops the worker with the app's lifespan |
| Entry point | `orbit/__main__.py` | `python -m orbit` runs uvicorn |

## Project layout

```
Orbit/
├── orbit/                      # application package
│   ├── __main__.py             # entry point (python -m orbit)
│   ├── api.py                  # REST API + app wiring
│   ├── config.py               # env-var settings
│   ├── docker_client.py        # Docker SDK wrapper
│   ├── health.py               # health evaluation + heartbeat
│   ├── recovery.py             # recovery engine
│   ├── store.py                # database models
│   └── worker.py               # monitor loop
├── tests/                      # pytest suite (fake Docker backend, no daemon needed)
├── examples/
│   └── docker-compose.demo.yml # healthy, flaky and broken demo containers
├── .github/workflows/ci.yml    # lint + tests + image build on every push
├── Dockerfile                  # Orbit image
├── docker-compose.yml          # Orbit + PostgreSQL deployment
├── .env.example                # all settings with defaults
├── requirements.txt            # runtime deps
├── requirements-dev.txt        # + pytest, httpx, ruff
└── pyproject.toml              # ruff + pytest config
```

## Quick start (deploy in 5 steps)

**Prerequisites:** a Linux host with Docker Engine 24+ and the Docker Compose plugin.

**1. Get the code**

```bash
git clone https://github.com/mohamxd1/orbit.git
cd orbit
```

**2. Configure**

```bash
cp .env.example .env
# at minimum, change POSTGRES_PASSWORD
```

**3. Build and start Orbit and PostgreSQL**

```bash
docker compose up -d --build
```

This starts two services:
- `db`: PostgreSQL 16 with a named volume, so history survives restarts.
- `orbit`: the API on port 8080, plus the monitor worker. It mounts
  `/var/run/docker.sock` so it can inspect and restart containers. It also
  creates the `orbit` Docker network that monitored apps join so heartbeats
  can reach them.

**4. Check that Orbit is up**

```bash
curl localhost:8080/health
# {"status":"ok","docker":true,"worker":true}
```

**5. (Optional) Watch it heal something**

```bash
docker compose -f examples/docker-compose.demo.yml up -d
```

- `web`: nginx with a heartbeat. It stays healthy.
- `flaky`: exits every 30s. Orbit restarts it and logs a `resolved` event.
- `broken`: exits immediately. Orbit tries twice, logs `failed`, then
  `skipped_threshold`, and flags it as `given_up`.

```bash
watch -n2 'curl -s localhost:8080/status | python3 -m json.tool'
curl -s localhost:8080/history | python3 -m json.tool
curl -s localhost:8080/metrics
```

Clean up the demo with `docker compose -f examples/docker-compose.demo.yml down`.

## Putting your own containers under Orbit

Orbit only touches containers you opt in with labels:

```yaml
services:
  api:
    image: my-api:latest
    healthcheck:                               # optional, recommended
      test: ["CMD", "curl", "-f", "http://localhost:8000/healthz"]
      interval: 10s
    labels:
      orbit.enable: "true"                     # required: track this container
      orbit.heartbeat.url: "http://api:8000/healthz"   # optional HTTP heartbeat
      orbit.cooldown: "30"                     # optional per-container override (seconds)
      orbit.max_restarts: "5"                  # optional per-container override
    networks: [orbit]                          # needed if you use a heartbeat URL

networks:
  orbit:
    external: true
```

The heartbeat URL is called from inside the Orbit container. Use the
service name on the shared `orbit` network, not `localhost`.

## Configuration

All settings are environment variables. Defaults are shown; see `.env.example`.

| Variable | Default | Meaning |
|---|---|---|
| `ORBIT_DATABASE_URL` | `sqlite:///./orbit.db` | SQLAlchemy URL. Compose sets it to PostgreSQL (`postgresql+psycopg://…`) |
| `ORBIT_LABEL` | `orbit.enable` | Label that marks a container as tracked (value `true`) |
| `ORBIT_POLL_INTERVAL` | `10` | Seconds between monitor passes |
| `ORBIT_FAILURE_THRESHOLD` | `2` | Consecutive failed checks before acting |
| `ORBIT_COOLDOWN_SECONDS` | `60` | Minimum gap between restarts of one container |
| `ORBIT_MAX_RESTARTS` | `3` | Restarts allowed per container per window |
| `ORBIT_RESTART_WINDOW_SECONDS` | `600` | Length of that rolling window |
| `ORBIT_VALIDATION_TIMEOUT` | `60` | Max seconds to wait for a restarted container to be healthy |
| `ORBIT_VALIDATION_STABLE_SECONDS` | `10` | How long it must stay healthy to count as recovered |
| `ORBIT_HEARTBEAT_TIMEOUT` | `3` | Timeout per heartbeat request |
| `ORBIT_HOST` / `ORBIT_PORT` | `0.0.0.0` / `8080` | API bind address |
| `ORBIT_PUBLISH_PORT` | `8080` | Host port Compose publishes the API on |
| `ORBIT_LOG_LEVEL` | `INFO` | Log level |

## API reference

Interactive docs are served at `http://localhost:8080/docs` (Swagger UI).

| Endpoint | Description |
|---|---|
| `GET /health` | Orbit's own liveness. `200` if Docker is reachable and the worker is running, otherwise `503` |
| `GET /status` | Every tracked container: status, health, reason, consecutive failures, restart count, last restart |
| `GET /metrics` | Uptime, tracked count, containers by health, total, successful and failed recoveries, success rate, given-up list |
| `GET /worker` | Monitor loop: running, poll count, last poll time and duration, last error |
| `GET /history?container=<name>&limit=100` | Recovery events, newest first, optionally for one container |

Recovery event actions: `restart` (outcome `resolved` or `failed`),
`skipped_threshold` (restart limit hit), `restart_error` (Docker refused the
restart).

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

pytest -q                       # unit + API tests, no Docker daemon needed
ruff check . && ruff format --check .

# run against your local Docker with SQLite:
python -m orbit                 # API on http://localhost:8080
```

The tests use an in-memory fake Docker backend (`tests/conftest.py`). They
cover debouncing, cooldown, restart limits, heartbeats, validation (including
a container that crashes right after restart), restart errors, and every API
endpoint. CI (`.github/workflows/ci.yml`) runs lint, the tests and a
`docker build` on every push.

## How the project was built (step by step)

This is the order the pieces were built in. Each step depends only on the
ones before it, so it is also a good order to read the code.

1. **Define the contract first.** The README described the behaviour:
   detect, restart safely, validate, audit, expose over REST. That became
   the five API endpoints and the two tables.
2. **Configuration (`config.py`).** Every tunable became an `ORBIT_*`
   environment variable in one frozen `Settings` dataclass. That is what
   makes the same image work in dev, CI and prod.
3. **State store (`store.py`).** Two SQLAlchemy models:
   `container_state` (latest view per container, keyed by name so it
   survives container re-creation) and `recovery_event` (append-only audit
   log). SQLite in dev, PostgreSQL in prod, same code.
4. **Docker abstraction (`docker_client.py`).** A small `DockerBackend`
   protocol with a real Docker SDK implementation. Everything above it talks
   to the protocol, so tests can run without a daemon.
5. **Health evaluation (`health.py`).** A pure function from a container
   snapshot to a verdict. Exited means unhealthy, the Docker health check is
   respected, "starting" gets no verdict, and an optional heartbeat catches
   hung processes.
6. **Recovery engine (`recovery.py`).** The safety logic, in order:
   cooldown, then the rolling-window restart limit, then restart, then
   validation, then an audit event. Validation needs the container to stay
   healthy for a stabilization period, so a process that starts and
   immediately crashes is recorded as `failed`, not `resolved`.
7. **Monitor worker (`worker.py`).** A background thread that runs one pass
   per interval. It updates state, debounces with consecutive-failure
   counts, hands off to the engine, and marks containers that disappeared as
   `missing`. Exceptions are caught and reported on `/worker`, so the loop
   never dies silently.
8. **API (`api.py`).** A FastAPI app factory that wires store, Docker,
   worker and routes together. It starts and stops the worker with the
   app's lifespan.
9. **Tests (`tests/`).** A fake Docker backend plus an injectable clock
   and sleep, so every recovery path is tested in under a second.
10. **Packaging (`Dockerfile`, `docker-compose.yml`).** A slim Python image
    with its own `HEALTHCHECK`, and Compose with PostgreSQL, a persistent
    volume, the Docker socket mount and the shared `orbit` network.
11. **Demo (`examples/`).** Healthy, flaky and broken workloads that show
    each code path end to end.
12. **CI (`.github/workflows/ci.yml`).** Lint, tests and an image build on
    every push and pull request.
