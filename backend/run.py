"""Server entrypoint.

Use this rather than invoking `uvicorn app.main:app` directly: on Windows,
uvicorn builds a ProactorEventLoop, which psycopg3 cannot use, and the choice
has to be overridden at startup rather than from inside the app. See
app.compat.uvicorn_loop_setting.

No-op difference on Linux and macOS, so the same command works everywhere.
"""

import os

from app.compat import uvicorn_loop_setting


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "0.0.0.0"),  # noqa: S104 -- containers must bind all interfaces
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("RELOAD", "false").lower() == "true",
        loop=uvicorn_loop_setting(),
    )


if __name__ == "__main__":
    main()
