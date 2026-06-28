"""NATS subjects for dashboard hub RPC (#1771)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class DashboardSubjects(BaseModel):
    sessions_list: Literal["factory.dashboard.sessions.list"] = (
        "factory.dashboard.sessions.list"
    )
    sessions_resume: Literal["factory.dashboard.sessions.resume"] = (
        "factory.dashboard.sessions.resume"
    )
    agents_status: Literal["factory.dashboard.agents.status"] = (
        "factory.dashboard.agents.status"
    )
    sessions_turns: Literal["factory.dashboard.sessions.turns"] = (
        "factory.dashboard.sessions.turns"
    )
    jobs_list: Literal["factory.dashboard.jobs.list"] = "factory.dashboard.jobs.list"


SUBJECTS = DashboardSubjects()