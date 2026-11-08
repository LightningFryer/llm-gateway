"""Authentication and authorization utilities for Relay.

Handles SHA-256 API key hashing, bearer token extraction, and admin authorization.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from relay.config import Settings, get_settings
from relay.db import ApiKeyRecord, get_api_key_by_hash

security = HTTPBearer(auto_error=False)


def hash_api_key(plain_key: str) -> str:
    """Returns SHA-256 hex digest of a raw API key secret."""
    return hashlib.sha256(plain_key.strip().encode("utf-8")).hexdigest()


def generate_api_key(prefix: str = "rl_live_") -> tuple[str, str, str]:
    """Generates a secure random API key string, its SHA-256 hash, and its display prefix."""
    random_part = secrets.token_urlsafe(24)
    raw_key = f"{prefix}{random_part}"
    key_hash = hash_api_key(raw_key)
    display_prefix = raw_key[:12]
    return raw_key, key_hash, display_prefix


async def get_current_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(security)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ApiKeyRecord:
    """FastAPI dependency: authenticates an API key from the Authorization header."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": "You must provide an API key in the Authorization header (Bearer <key>).",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "invalid_api_key",
                }
            },
        )

    raw_token = credentials.credentials
    if raw_token == settings.relay_admin_key:
        return ApiKeyRecord(
            id="admin-master-key",
            name="Admin Master Key",
            key_hash=hash_api_key(raw_token),
            key_prefix="admin",
            rpm_limit=100000,
            tpm_limit=100000000,
            monthly_budget_usd=999999.0,
            allowed_aliases=["*"],
            enabled=True,
            created_at=datetime.now(UTC).isoformat(),
        )

    key_hash = hash_api_key(raw_token)
    api_key = await get_api_key_by_hash(settings.database_path, key_hash)

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": "Incorrect API key provided.",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "invalid_api_key",
                }
            },
        )

    if not api_key.enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "message": "This API key has been disabled.",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "api_key_disabled",
                }
            },
        )

    return api_key


async def verify_admin_key(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(security)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    """FastAPI dependency: verifies the admin secret token for /admin endpoints."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": "Admin authorization header required.",
                    "type": "auth_error",
                    "code": 401,
                }
            },
        )

    if credentials.credentials != settings.relay_admin_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {"message": "Invalid admin token.", "type": "auth_error", "code": 403}
            },
        )

    return credentials.credentials
