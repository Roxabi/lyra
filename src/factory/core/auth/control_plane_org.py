"""Organization models for control-plane (ADR-103 Block 4)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

__all__ = ["OrgMember", "OrgRecord", "OrgRole"]


class OrgRole(str, Enum):
    OWNER = "owner"
    MEMBER = "member"


@dataclass(frozen=True, slots=True)
class OrgRecord:
    id: str
    name: str
    created_by: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OrgMember:
    org_id: str
    user_id: str
    org_role: OrgRole
