"""LLM-domain NATS subject strings and per-worker helpers.

Canonical values — literal strings (no f-strings, no derivation) so grep
can locate every reference across the monorepo.
"""

from dataclasses import dataclass
from typing import Literal

from roxabi_contracts._nats_utils import validate_worker_id

__all__ = ["SUBJECTS", "per_worker_llm", "validate_worker_id"]


@dataclass(frozen=True, slots=True)
class _Subjects:
    """Frozen namespace of LLM-domain subject strings.

    Attribute access is pyright-checked: typos fail at type-check time
    rather than silently returning None.
    """

    generate_request: Literal["factory.llm.generate.request"] = (
        "factory.llm.generate.request"
    )
    heartbeat: Literal["factory.llm.heartbeat"] = "factory.llm.heartbeat"
    llm_workers: Literal["llm-workers"] = "llm-workers"
    lifecycle_swap: Literal["factory.llm.lifecycle.swap"] = "factory.llm.lifecycle.swap"
    lifecycle_stop: Literal["factory.llm.lifecycle.stop"] = "factory.llm.lifecycle.stop"
    lifecycle_status: Literal["factory.llm.lifecycle.status"] = (
        "factory.llm.lifecycle.status"
    )
    lifecycle_list: Literal["factory.llm.lifecycle.list"] = "factory.llm.lifecycle.list"
    lifecycle_reload_catalog: Literal["factory.llm.lifecycle.reload-catalog"] = (
        "factory.llm.lifecycle.reload-catalog"  # noqa: E501
    )


SUBJECTS = _Subjects()


def per_worker_llm(worker_id: str) -> str:
    """Per-worker LLM request subject: ``factory.llm.generate.request.{worker_id}``.

    Raises ``ValueError`` if ``worker_id`` contains characters outside
    ``[A-Za-z0-9_-]`` — see ``validate_worker_id``.
    """
    validate_worker_id(worker_id)
    return f"{SUBJECTS.generate_request}.{worker_id}"
