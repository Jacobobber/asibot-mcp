"""Shared fixtures for PostgreSQL integration tests.

Reads the test database URL from the ``ASIBOT_TEST_DATABASE_URL`` environment
variable (default: ``postgresql://asibot:asibot@localhost:5432/asibot_test``).

The module-scoped ``backend`` fixture:
  1. Connects to PostgreSQL and drops all known tables (clean slate).
  2. Calls ``backend.initialize()`` to create schema and run migrations.
  3. Yields the live ``PostgresBackend`` instance to tests.
  4. Tears down by dropping every table and closing the pool.

If PostgreSQL is unreachable the entire integration test module is skipped.
"""

from __future__ import annotations

import asyncio
import os

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get(
    "ASIBOT_TEST_DATABASE_URL",
    "postgresql://asibot:asibot@localhost:5432/asibot_test",
)

# Tables managed by the backend, in DROP order (respects FK dependencies).
_ALL_TABLES = [
    "microsoft_tokens",
    "credentials",
    "preferences",
    "sessions",
    "audit_log",
    "schema_migrations",
    "users",
]


# ---------------------------------------------------------------------------
# Connectivity check
# ---------------------------------------------------------------------------

def _pg_is_reachable() -> bool:
    """Return True if we can open a connection to the test database."""
    import asyncpg

    async def _probe() -> bool:
        try:
            conn = await asyncpg.connect(DATABASE_URL, timeout=5)
            await conn.close()
            return True
        except Exception:
            return False

    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_probe())


# Skip the whole module when PostgreSQL is not available.
pytestmark = pytest.mark.skipif(
    not _pg_is_reachable(),
    reason="PostgreSQL not reachable at " + DATABASE_URL,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

async def _drop_all_tables(pool) -> None:
    """Drop every known table (IF EXISTS, CASCADE) for a clean slate."""
    async with pool.acquire() as conn:
        for table in _ALL_TABLES:
            await conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")


@pytest_asyncio.fixture(scope="module")
async def backend():
    """Provide an initialised PostgresBackend against the test database.

    Setup : drop pre-existing tables -> initialize (schema + migrations).
    Teardown: drop all tables -> close pool.
    """
    import asyncpg
    from asibot.db_postgres import PostgresBackend

    # Pre-clean: connect directly to drop stale tables from previous runs.
    temp_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
    await _drop_all_tables(temp_pool)
    await temp_pool.close()

    # Create and initialise the backend under test.
    be = PostgresBackend(DATABASE_URL, min_size=2, max_size=10)
    await be.initialize()

    yield be

    # Teardown: clean up tables and close the pool.
    pool = be._get_pool()
    await _drop_all_tables(pool)
    await be.close()


@pytest_asyncio.fixture(scope="module")
async def pool(backend):
    """Expose the raw asyncpg pool for low-level assertions."""
    return backend._get_pool()
