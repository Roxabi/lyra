"""Webhook signature verification for GitHub and Cloudflare."""

from __future__ import annotations

import hashlib
import hmac


def verify_github_signature(
    body: bytes, header: str | None, secret: str
) -> bool:
    """Validate ``X-Hub-Signature-256`` (``sha256=<hex>``)."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    received = header[7:]
    return hmac.compare_digest(expected, received)


def verify_cloudflare_auth(header: str | None, secret: str) -> bool:
    """Validate Cloudflare webhook ``cf-webhook-auth`` header."""
    if not header:
        return False
    return hmac.compare_digest(header, secret)