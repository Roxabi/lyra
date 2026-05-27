"""Event and metric contract models for the observability bus.

Two envelope models:
  LyraEvent  — structured operational events (lifecycle, errors, alerts)
  LyraMetric — typed metrics (counters, gauges, histograms)

Canonical subject patterns:
  lyra.event.<service>.<kind>
  lyra.metric.<service>.<name>
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import StringConstraints, model_validator

from roxabi_contracts.envelope import ContractEnvelope


class LyraEvent(ContractEnvelope):
    """Structured operational event. Published to ``lyra.event.<service>.<kind>``."""

    service: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]*$")]
    kind: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9._-]*$")]
    level: Literal["debug", "info", "warn", "error", "critical"]
    message: str | None = None
    payload: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _error_level_requires_message(self) -> Self:
        if self.level in ("error", "critical") and not self.message:
            raise ValueError(
                "LyraEvent with level='error' or 'critical' must carry a message"
            )
        return self


class LyraMetric(ContractEnvelope):
    """Typed metric. Published to ``lyra.metric.<service>.<name>``."""

    service: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]*$")]
    name: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9._-]*$")]
    metric_type: Literal["counter", "gauge", "histogram"]
    value: float
    unit: str | None = None
    labels: dict[str, str] | None = None
