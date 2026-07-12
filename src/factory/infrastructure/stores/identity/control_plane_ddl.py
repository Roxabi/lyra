"""DDL + shared helpers for control-plane identity tables (ADR-103)."""

from __future__ import annotations

from datetime import datetime, timezone

from factory.infrastructure.stores.identity.user_store import _CREATE_USERS

__all__ = [
    "API_KEY_PREFIX",
    "DDL_CONTROL_PLANE",
    "JOB_META_DDL",
    "ORG_DDL",
    "SESSION_COOKIE_NAME",
    "_CREATE_API_KEYS",
    "_CREATE_INVITES",
    "_CREATE_SESSIONS",
    "_normalize_email",
    "_parse_ts",
    "_utc_now",
]

SESSION_COOKIE_NAME = "factory_session"
API_KEY_PREFIX = "fak_"

_CREATE_INVITES = """
CREATE TABLE IF NOT EXISTS dash_invites (
    id          TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    invited_by  TEXT NOT NULL,
    status      TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS dash_sessions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    expires_at  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_CREATE_API_KEYS = """
CREATE TABLE IF NOT EXISTS dash_api_keys (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    key_hash    TEXT NOT NULL UNIQUE,
    prefix      TEXT NOT NULL,
    scopes      TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    revoked_at  TEXT
)
"""

_CREATE_ORGS = """
CREATE TABLE IF NOT EXISTS organizations (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_CREATE_ORG_MEMBERS = """
CREATE TABLE IF NOT EXISTS org_members (
    org_id      TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    org_role    TEXT NOT NULL,
    PRIMARY KEY (org_id, user_id)
)
"""

_CREATE_JOB_LAUNCHES = """
CREATE TABLE IF NOT EXISTS dash_job_launches (
    job_id       TEXT PRIMARY KEY,
    launched_by  TEXT NOT NULL,
    org_id       TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

ORG_DDL: tuple[str, ...] = (_CREATE_ORGS, _CREATE_ORG_MEMBERS)
JOB_META_DDL: tuple[str, ...] = (_CREATE_JOB_LAUNCHES,)

DDL_CONTROL_PLANE: tuple[str, ...] = (
    _CREATE_USERS,
    _CREATE_INVITES,
    _CREATE_SESSIONS,
    _CREATE_API_KEYS,
    *ORG_DDL,
    *JOB_META_DDL,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(raw: str) -> datetime:
    ts = datetime.fromisoformat(raw)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _normalize_email(email: str) -> str:
    return email.strip().lower()
