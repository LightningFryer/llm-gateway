"""Pytest fixtures for unit and integration testing."""

import asyncio
import os
import tempfile
from collections.abc import Iterator

import fakeredis
import fakeredis.aioredis as fake_aioredis
import pytest
from fastapi.testclient import TestClient

from relay.auth import generate_api_key
from relay.config import Settings, get_settings
from relay.db import create_api_key, init_db


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
def fake_redis() -> Iterator[fake_aioredis.FakeRedis]:
    """Provides an isolated in-memory fakeredis client for testing."""
    server = fakeredis.FakeServer()
    client = fake_aioredis.FakeRedis(server=server, decode_responses=True)
    yield client


@pytest.fixture
def app_with_db(temp_db_path: str) -> Iterator[TestClient]:
    """Provides a TestClient wired with an initialized temporary database."""

    def get_test_settings() -> Settings:
        return Settings(
            database_path=temp_db_path,
            relay_admin_key="test_admin_key",
        )

    from relay.main import app

    app.dependency_overrides[get_settings] = get_test_settings

    asyncio.run(init_db(temp_db_path))

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
def auth_key(temp_db_path: str) -> tuple[str, str]:
    """Creates a valid test API key in SQLite and returns (raw_key, key_id)."""
    raw_key, key_hash, prefix = generate_api_key()
    asyncio.run(init_db(temp_db_path))
    rec = asyncio.run(
        create_api_key(
            db_path=temp_db_path,
            name="Test Suite Key",
            key_hash=key_hash,
            key_prefix=prefix,
            allowed_aliases=["*"],
        )
    )
    return raw_key, rec.id
