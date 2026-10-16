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
