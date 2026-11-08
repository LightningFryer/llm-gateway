"""Pytest fixtures for unit and integration testing."""

import os
import tempfile
from collections.abc import AsyncIterator, Iterator

import fakeredis.aioredis as fake_aioredis
import pytest
from fastapi.testclient import TestClient

from relay.config import Settings, get_settings
from relay.db import init_db


@pytest.fixture
def temp_db_path() -> Iterator[str]:
    """Provides a path to an isolated temporary SQLite database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    if os.path.exists(path):
        os.remove(path)
    # Also clean up WAL and SHM files if created
    for ext in ["-wal", "-shm"]:
        if os.path.exists(path + ext):
            os.remove(path + ext)


@pytest.fixture
def fake_redis() -> AsyncIterator[fake_aioredis.FakeRedis]:
    """Provides an isolated in-memory fakeredis client for testing."""
    server = fake_aioredis.FakeServer()
    client = fake_aioredis.FakeRedis(server=server, decode_responses=True)
    yield client


@pytest.fixture
def app_with_db(temp_db_path: str) -> Iterator[TestClient]:
    """Provides a TestClient wired with an initialized temporary database."""

    # Override settings
    def get_test_settings() -> Settings:
        return Settings(
            database_path=temp_db_path,
            relay_admin_key="test_admin_key",
        )

    from relay.main import app

    app.dependency_overrides[get_settings] = get_test_settings

    # Run DB init synchronously for test setup
    import asyncio

    asyncio.run(init_db(temp_db_path))

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()
