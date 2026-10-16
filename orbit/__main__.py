"""Entry point: ``python -m orbit``."""

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "orbit.api:create_app",
        factory=True,
        host=os.environ.get("ORBIT_HOST", "0.0.0.0"),
        port=int(os.environ.get("ORBIT_PORT", "8080")),
        log_level=os.environ.get("ORBIT_LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
