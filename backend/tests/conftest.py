"""Test fixtures.

Runs against a real Postgres (from docker-compose locally, or a service
container in CI) -- mocking the database hides exactly the bugs that matter.
Set TEST_DATABASE_URL to override.
"""

import asyncio
import os
import sys
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import get_db
from app.main import app
from app.models import Base


def pytest_asyncio_loop_factories(config: object, item: object) -> dict[str, object] | None:
    """psycopg3 cannot run on Windows' default ProactorEventLoop -- see app.compat.

    Uses pytest-asyncio's loop-factory hook rather than overriding the
    `event_loop_policy` fixture, which is deprecated, and avoids the
    likewise-deprecated asyncio policy API.
    """
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return None


TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://safeshield:safeshield@localhost:5432/safeshield_test",
)


_pgvector_available = False


def pgvector_available() -> bool:
    """Whether the test database has the pgvector extension.

    CI uses the pgvector/pgvector image so this is always true there. A plain
    local Postgres may not have it, in which case vector-dependent tests skip
    instead of failing the entire suite.
    """
    return _pgvector_available


needs_pgvector = pytest.mark.skipif(
    not pgvector_available, reason="pgvector extension not available"
)


@pytest.fixture(scope="session")
async def engine() -> AsyncGenerator:
    global _pgvector_available

    eng = create_async_engine(TEST_DATABASE_URL, poolclass=None)

    async with eng.begin() as conn:
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            _pgvector_available = True
        except Exception:  # noqa: BLE001 -- absence is expected on plain Postgres
            _pgvector_available = False

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        if _pgvector_available:
            await conn.run_sync(Base.metadata.create_all)
        else:
            # Everything except `chunks`, whose vector column needs the extension.
            tables = [t for name, t in Base.metadata.tables.items() if name != "chunks"]
            await conn.run_sync(Base.metadata.create_all, tables=tables)

    yield eng
    await eng.dispose()


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
