"""Background worker entrypoint.

Use this rather than `arq app.worker.tasks.WorkerSettings`.

The arq CLI builds its own event loop, which on Windows is a ProactorEventLoop.
psycopg3 cannot run on that, so the worker starts, accepts jobs, and then fails
every single one at the first database call -- documents sit on "pending"
forever with the error buried in the worker log. Driving `Worker.async_run()`
under a selector loop fixes it.

No-op difference on Linux, so the same command works everywhere.
"""

from arq.worker import create_worker

from app.compat import asyncio_run
from app.worker.tasks import WorkerSettings


async def _run() -> None:
    # Constructed inside the running loop so the worker adopts it.
    worker = create_worker(WorkerSettings)  # type: ignore[arg-type]
    await worker.async_run()


def main() -> None:
    asyncio_run(_run())


if __name__ == "__main__":
    main()
