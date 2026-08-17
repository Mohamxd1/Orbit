# Orbit

**Self-Healing Container Orchestration Platform**

Orbit is an autonomous monitoring and recovery service for Docker
containers. It continuously watches container health, detects failures
through health checks and heartbeat validation, and automatically restarts
unhealthy services — while using cooldown timers and restart-threshold
limits to prevent restart loops. Every action is logged to a persistent
state store for full auditability, and a REST API exposes real-time status,
metrics, and recovery history.

## Features

- **Autonomous monitoring** — continuously polls all tracked Docker
  containers via the Docker SDK
- **Smart health detection** — combines Docker health checks with custom
  heartbeat validation to catch failures Docker alone might miss
- **Safe auto-recovery** — a recovery engine with cooldown timers and
  restart-threshold limits auto-heals failed containers without risking
  crash-restart loops
- **Post-restart validation** — confirms a container is actually healthy
  again before marking an incident resolved
- **Full audit trail** — a persistent state store tracks container health,
  restart counts, and complete recovery history
- **REST API** — exposes health, status, metrics, worker, and history
  endpoints for real-time monitoring and dashboard integration

## Tech Stack

- **Language:** Python
- **API Framework:** FastAPI
- **Container Integration:** Docker SDK for Python
- **Database:** PostgreSQL (production) / SQLite (development)

## API Endpoints

| Endpoint    | Description                                             |
|-------------|----------------------------------------------------------|
| `/health`   | Orbit's own liveness check                               |
| `/status`   | Current health of all tracked containers                 |
| `/metrics`  | Aggregate stats (uptime, restart counts, etc.)            |
| `/worker`   | Monitoring loop status and last poll time                |
| `/history`  | Historical recovery events, filterable by container       |

## How It Works

1. The **monitor service** polls all tracked containers on a fixed
   interval, checking Docker health status and heartbeat signals.
2. If a container is found unhealthy, the **recovery engine** checks
   whether it's within its cooldown window and under its restart
   threshold before acting — this is what prevents runaway restart loops.
3. If clear to act, Orbit restarts the container and then **validates
   recovery** by re-checking health afterward.
4. Every check, decision, and action is written to the **state store**,
   giving a complete history of what happened and why.
5. The **REST API** surfaces all of this data for monitoring or dashboard
   use.

## Status

Built in 2026 as a self-hosted alternative to heavier orchestration tools
for environments running plain Docker without Kubernetes.
