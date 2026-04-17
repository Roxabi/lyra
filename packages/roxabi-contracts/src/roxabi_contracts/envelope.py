"""Envelope base for all roxabi-contracts domain models.

See docs/architecture/adr/049-roxabi-contracts-shared-schema-package.mdx.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

# ADR-044 — single source of truth for the wire-protocol contract version. All
# producer sites (hub clients + satellite adapters) stamp this on outgoing
# payloads. Consumers ignore unknown values. Bumping requires a new ADR.
CONTRACT_VERSION = "1"

# Import-time validation: a typo in CONTRACT_VERSION must crash at load, not
# drop every inbound envelope at runtime. check_contract_version relies on
# ``int(expected)`` succeeding — this assert is the single gate that guarantees
# that invariant for every call site.
assert CONTRACT_VERSION.isdigit() and int(CONTRACT_VERSION) > 0, (  # noqa: S101
    f"CONTRACT_VERSION must be a positive decimal string, got {CONTRACT_VERSION!r}"
)


class ContractEnvelope(BaseModel):
    """Common base for every per-domain NATS contract model.

    All subclasses inherit ``extra="ignore"`` so a v0.1.0 consumer
    receiving a v0.2.0 payload with new optional fields parses cleanly.
    Unknown fields are silently dropped (ADR-049 §Versioning).

    .. warning::
       Consumers MUST NOT call ``model_validate_json()`` or
       ``model_validate()`` directly on raw NATS ``msg.data`` bytes.
       The correct call site is ``roxabi_nats.deserialize()``, which
       enforces the pre-validation byte-size gate before Pydantic
       parses the payload (ADR-049 §Trust Model).
    """

    # Forward-compat via extra="ignore": unknown fields from a newer
    # payload version parse cleanly (ADR-049 §Versioning). This makes
    # additive field introduction safe — EXCEPT for security-bearing
    # fields (caller identity, auth scopes, signed tokens, audit
    # provenance). Any such field MUST be introduced via a MAJOR bump
    # with coordinated satellite upgrade, NEVER as an additive minor.
    # A v1 satellite silently ignoring an auth_token field is exactly
    # the bug class this exclusion prevents (ADR-049 §Versioning
    # §Security-bearing fields exclusion). Each domain contract ADR
    # must list which fields (if any) are security-bearing.
    model_config = ConfigDict(extra="ignore")

    contract_version: str
    trace_id: Annotated[str, StringConstraints(min_length=1)]
    issued_at: datetime
