"""Data models for monitoring checks and diagnosis reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CheckResult:
    """Result of a single Layer 1 health check."""

    name: str
    passed: bool
    detail: str
    timestamp: datetime


@dataclass(frozen=True)
class HealthReport:
    """Aggregated result of all Layer 1 checks."""

    checks: list[CheckResult]
    all_passed: bool
    failed_count: int
    timestamp: datetime


@dataclass(frozen=True)
class DiagnosisReport:
    """Layer 2 LLM diagnosis of a health anomaly."""

    severity: str  # info | warning | critical
    diagnosis: str
    suggested_remediation: str
    source: HealthReport
