"""Social media tool-domain NATS subject strings and per-worker helpers.

Canonical tool-nature prefix: ``factory.tool.socialmedia.*`` (ADR-049 / #1713).
"""

from dataclasses import dataclass
from typing import Literal

from roxabi_contracts._nats_utils import validate_worker_id

__all__ = ["SUBJECTS", "per_worker_socialmedia", "validate_worker_id"]


@dataclass(frozen=True, slots=True)
class _Subjects:
    list_groups: Literal["factory.tool.socialmedia.list_groups"] = (
        "factory.tool.socialmedia.list_groups"
    )
    list_integrations: Literal["factory.tool.socialmedia.list_integrations"] = (
        "factory.tool.socialmedia.list_integrations"
    )
    publish: Literal["factory.tool.socialmedia.publish"] = (
        "factory.tool.socialmedia.publish"
    )
    schedule: Literal["factory.tool.socialmedia.schedule"] = (
        "factory.tool.socialmedia.schedule"
    )
    heartbeat: Literal["factory.tool.socialmedia.heartbeat"] = (
        "factory.tool.socialmedia.heartbeat"
    )
    workers: Literal["socialmedia_workers"] = "socialmedia_workers"


SUBJECTS = _Subjects()

_ACTIONS = frozenset({"list_groups", "list_integrations", "publish", "schedule"})


def per_worker_socialmedia(worker_id: str, action: str) -> str:
    """Per-worker subject, e.g. ``factory.tool.socialmedia.publish.{worker_id}``."""
    validate_worker_id(worker_id)
    if action not in _ACTIONS:
        raise ValueError(f"unknown socialmedia action: {action!r}")
    return f"factory.tool.socialmedia.{action}.{worker_id}"