"""NATS subjects for fleet container reports."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class FleetSubjects(BaseModel):
    container_report: Literal["factory.metric.host.container_report"] = (
        "factory.metric.host.container_report"
    )


CONTAINER_REPORT = "factory.metric.host.container_report"
SUBJECTS = FleetSubjects()