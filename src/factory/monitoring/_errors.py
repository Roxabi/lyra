"""Shared exception tuples for monitoring health probes."""

from __future__ import annotations

import asyncio
import json

import httpx

_MONITORING_HTTP_ERRORS: tuple[type[BaseException], ...] = (
    httpx.HTTPError,
    httpx.TimeoutException,
    json.JSONDecodeError,
    ValueError,
    TypeError,
)

_MONITORING_ESCALATION_ERRORS: tuple[type[BaseException], ...] = (
    RuntimeError,
    asyncio.TimeoutError,
    TimeoutError,
    json.JSONDecodeError,
    KeyError,
    httpx.HTTPError,
    httpx.TimeoutException,
)

__all__ = ["_MONITORING_ESCALATION_ERRORS", "_MONITORING_HTTP_ERRORS"]