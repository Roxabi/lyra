"""MessageLifecycleHooks — zero-dep OTel instrumentation protocol (#2069)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable


@runtime_checkable
class MessageLifecycleHooks(Protocol):
    """Best-effort lifecycle callbacks invoked by NatsAdapterBase._dispatch()."""

    def on_work_start(  # noqa: PLR0913 — protocol mirrors envelope fields
        self,
        *,
        trace_id: str,
        job_id: str,
        parent_job_id: str | None,
        pool_id: str | None,
        subject: str,
        envelope_name: str,
        queue_group: str,
    ) -> None: ...

    def on_work_end(
        self,
        *,
        trace_id: str,
        job_id: str,
        duration_ms: float,
        error: BaseException | None = None,
    ) -> None: ...

    def record_domain_attrs(
        self, attrs: Mapping[str, str | int | float | bool]
    ) -> None: ...