"""Test fixtures.

Runs against a real Postgres (from docker-compose locally, or a service
container in CI) -- mocking the database hides exactly the bugs that matter.
Set TEST_DATABASE_URL to override.
"""

import asyncio
import os
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic import command
from app.db import get_db
from app.main import app
from app.models import Base


def pytest_asyncio_loop_factories(config: object, item: object) -> dict[str, object]:
    """Run tests on a selector-based event loop.

    psycopg3 cannot use Windows' default ProactorEventLoop; SelectorEventLoop is
    already the default on Linux and macOS, so this is unconditional. It has to
    be: pytest-asyncio invokes the hook whenever it is implemented and requires a
    non-empty mapping back, so returning None on non-Windows platforms raised
    UsageError and aborted collection for every test -- a failure that could not
    reproduce on the Windows machine this was written on.

    Uses this hook rather than overriding the `event_loop_policy` fixture, which
    is deprecated, and avoids the likewise-deprecated asyncio policy API.
    """
    return {"selector": asyncio.SelectorEventLoop}


TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://safeshield:safeshield@localhost:5432/safeshield_test",
)


def _probe_pgvector() -> bool:
    """Whether the test database has the pgvector extension.

    Probed synchronously at import time because `skipif` is evaluated during
    collection, before any fixture has run. The previous version set a module
    global inside the session `engine` fixture and read it from `skipif`, which
    could only ever observe the initial `False` -- and it passed the *function*
    to `skipif` rather than calling it, so `not <function>` was constantly False
    and nothing was ever skipped in the first place. Both halves were broken in
    opposite directions, which is why it looked like it worked.

    CI uses the pgvector/pgvector image, so this is always true there; a plain
    local Postgres skips the vector tests instead of failing the whole suite.
    """
    from sqlalchemy import create_engine

    # No driver rewrite: psycopg3 serves sync and async under one URL scheme.
    # Stripping `+psycopg` would select psycopg2, which is not installed, and
    # the probe would report "no pgvector" on a database that has it.
    try:
        engine = create_engine(TEST_DATABASE_URL, connect_args={"connect_timeout": 5})
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        engine.dispose()
    except Exception:  # noqa: BLE001 -- absence and unreachability both mean "skip"
        return False
    return True


pgvector_available = _probe_pgvector()

needs_pgvector = pytest.mark.skipif(
    not pgvector_available, reason="pgvector extension not available"
)


def _run_migrations(sync_url: str) -> None:
    """Build the test schema by running the real migrations.

    Not `Base.metadata.create_all()`. That creates tables from the ORM models
    and silently omits everything a migration does in raw SQL -- the tsv trigger
    most importantly, which meant chunks were indexed with a NULL tsv in tests
    while working correctly in production. Migrating also means every test run
    re-proves the migrations apply cleanly.
    """
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", sync_url)
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    eng = create_async_engine(TEST_DATABASE_URL, poolclass=None)

    # _probe_pgvector only established that the extension *can* be created; the
    # schema drop below removes it again and migration 0002 recreates it.

    # Start from empty so a re-run is not affected by a previous schema.
    async with eng.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))

    if pgvector_available:
        # No driver rewrite: psycopg3 serves sync and async under one scheme.
        _run_migrations(TEST_DATABASE_URL)
    else:
        # No vector extension, so migration 0002 cannot run. Fall back to the
        # ORM schema minus `chunks`; the vector-dependent tests skip anyway.
        async with eng.begin() as conn:
            tables = [t for name, t in Base.metadata.tables.items() if name != "chunks"]
            await conn.run_sync(Base.metadata.create_all, tables=tables)

    yield eng
    await eng.dispose()


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> Generator[None, None, None]:
    """Clear rate-limit counters between tests.

    Limiter state is process-global and every test shares one client key, so
    without this the upload limit is exhausted partway through the suite and
    unrelated tests start failing with 429. Limits stay enabled so they are
    still exercised where a test asserts on them.
    """
    from app.core.ratelimit import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
async def db(engine) -> AsyncGenerator[AsyncSession, None]:
    """A session wrapped in a transaction that is rolled back after each test.

    `join_transaction_mode="create_savepoint"` makes the session operate inside a
    SAVEPOINT, so a rollback in application code (the duplicate-email path in
    /register does exactly this) unwinds only its own work instead of destroying
    the outer transaction that provides test isolation.
    """
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = async_sessionmaker(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )()
        yield session
        await session.close()
        await trans.rollback()


@pytest.fixture
async def client(db: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        """Mirror the production commit/rollback semantics of app.db.get_db.

        A bare `yield db` would neither commit nor roll back, so every write
        stays visible to later requests in the same test regardless of whether
        the endpoint succeeded. That masks real bugs -- notably a handler that
        writes and then raises, where production discards the write. Because the
        session joins the outer transaction via SAVEPOINT, committing here is
        still confined to the test and rolled back at teardown.
        """
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
