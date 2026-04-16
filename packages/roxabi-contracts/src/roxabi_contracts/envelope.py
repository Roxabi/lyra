"""Envelope base for all roxabi-contracts domain models.

See docs/architecture/adr/049-roxabi-contracts-shared-schema-package.mdx.
CONTRACT_VERSION is NOT defined here — it is migrated from
roxabi_nats.adapter_base in a follow-up issue (#765).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ContractEnvelope(BaseModel):
    """Common base for every per-domain NATS contract model.

    All subclasses inherit ``extra="ignore"`` so a v0.1.0 consumer
    receiving a v0.2.0 payload with new optional fields parses cleanly.
    Unknown fields are silently dropped (ADR-049 §Versioning).
    """

    model_config = ConfigDict(extra="ignore")

    contract_version: str
    trace_id: str
    issued_at: datetime
