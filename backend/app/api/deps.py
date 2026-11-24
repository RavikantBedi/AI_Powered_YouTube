"""API authentication. A single shared key guards every mutating route; swap for real
RBAC/JWT when multi-tenant (the dependency is the only place that needs to change)."""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from app.config import get_settings


async def require_api_key(x_api_key: str = Header(default="")) -> None:
    expected = get_settings().agency_api_key
    if not expected or expected == "change-me":
        # Refuse to run unprotected rather than silently accepting anything.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AGENCY_API_KEY is not configured — set it in the environment before use.")
    if not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing X-API-Key")
