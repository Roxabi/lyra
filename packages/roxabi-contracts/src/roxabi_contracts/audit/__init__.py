"""Audit-domain security event contracts.

Pure Pydantic. No NATS imports. No transport logic. Every model subclasses
ContractEnvelope, which provides (contract_version, trace_id, issued_at)
plus ConfigDict(extra="ignore") for forward-compat.
"""

from __future__ import annotations

from typing import Literal

from roxabi_contracts.envelope import ContractEnvelope


class SecurityEvent(ContractEnvelope):
    """Emitted on every CLI subprocess spawn.

    trace_id/issued_at/contract_version inherited from ContractEnvelope.
    """

    kind: Literal["cli.subprocess.spawned"]
    pool_id: str
    agent_name: str
    skip_permissions: bool  # True = --dangerously-skip-permissions active
    tools_restricted: bool  # True = explicit allowlist configured; False = all tools
    tools_allowlist: list[str]  # empty when tools_restricted=False
    model: str
    pid: int
