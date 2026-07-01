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
    jobs_launch: Literal["factory.dashboard.jobs.launch"] = (
        "factory.dashboard.jobs.launch"
    )
    jobs_steer: Literal["factory.dashboard.jobs.steer"] = "factory.dashboard.jobs.steer"
    jobs_cancel: Literal["factory.dashboard.jobs.cancel"] = "factory.dashboard.jobs.cancel"
    voice_capabilities: Literal["factory.dashboard.voice.capabilities"] = (
        "factory.dashboard.voice.capabilities"
    )
    agents_list: Literal["factory.dashboard.agents.list"] = (
        "factory.dashboard.agents.list"
    )
    agents_get: Literal["factory.dashboard.agents.get"] = "factory.dashboard.agents.get"
    agents_patch: Literal["factory.dashboard.agents.patch"] = (
        "factory.dashboard.agents.patch"
    )
    agents_create: Literal["factory.dashboard.agents.create"] = (
        "factory.dashboard.agents.create"
    )
    admin_access: Literal["factory.dashboard.admin.access"] = (
        "factory.dashboard.admin.access"
    )
    admin_user_create: Literal["factory.dashboard.admin.user.create"] = (
        "factory.dashboard.admin.user.create"
    )
    admin_user_patch: Literal["factory.dashboard.admin.user.patch"] = (
        "factory.dashboard.admin.user.patch"
    )
    agents_soul_put: Literal["factory.dashboard.agents.soul.put"] = (
        "factory.dashboard.agents.soul.put"
    )
    agents_soul_get: Literal["factory.dashboard.agents.soul.get"] = (
        "factory.dashboard.agents.soul.get"
    )
    agents_soul_preview: Literal["factory.dashboard.agents.soul.preview"] = (
        "factory.dashboard.agents.soul.preview"
    )
    fleet_list: Literal["factory.dashboard.fleet.list"] = "factory.dashboard.fleet.list"
    connectors_installations_list: Literal[
        "factory.dashboard.connectors.installations.list"
    ] = "factory.dashboard.connectors.installations.list"
    connectors_installations_upsert: Literal[
        "factory.dashboard.connectors.installations.upsert"
    ] = "factory.dashboard.connectors.installations.upsert"
    connectors_installations_delete: Literal[
        "factory.dashboard.connectors.installations.delete"
    ] = "factory.dashboard.connectors.installations.delete"
    pipeline_list: Literal["factory.dashboard.pipeline.list"] = (
        "factory.dashboard.pipeline.list"
    )


SUBJECTS = DashboardSubjects()