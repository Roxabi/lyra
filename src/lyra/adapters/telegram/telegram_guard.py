"""Webhook security helpers for the Telegram adapter."""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

__all__ = ["_make_verifier"]


def _make_verifier(secret: str):
    """Return a FastAPI dependency that validates the Telegram webhook secret."""

    async def verify(request: Request) -> None:
        incoming = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not secret or not hmac.compare_digest(incoming, secret):
            raise HTTPException(status_code=401, detail="Unauthorized")

    return verify
