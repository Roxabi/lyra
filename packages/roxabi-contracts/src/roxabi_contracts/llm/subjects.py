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

    generate_request: Literal["lyra.llm.generate.request"] = "lyra.llm.generate.request"
    heartbeat: Literal["lyra.llm.heartbeat"] = "lyra.llm.heartbeat"
    llm_workers: Literal["llm-workers"] = "llm-workers"


SUBJECTS = _Subjects()


def per_worker_llm(worker_id: str) -> str:
    """Per-worker LLM request subject: ``lyra.llm.generate.request.{worker_id}``.

    Raises ``ValueError`` if ``worker_id`` contains characters outside
    ``[A-Za-z0-9_-]`` — see ``validate_worker_id``.
    """
    validate_worker_id(worker_id)
    return f"{SUBJECTS.generate_request}.{worker_id}"
