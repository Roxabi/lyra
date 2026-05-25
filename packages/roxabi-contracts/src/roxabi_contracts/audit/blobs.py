"""BlobAuditEvent contract — one audit record per blob-store operation."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from roxabi_contracts.envelope import ContractEnvelope

__all__ = ["BlobAuditEvent"]


class BlobAuditEvent(ContractEnvelope):
    """Emitted on every BlobStore HTTP operation (put/get/exists/delete).

    trace_id/issued_at/contract_version inherited from ContractEnvelope.
    Published on ``lyra.audit.blobs.<op>`` — use :meth:`subject_for` to
    derive the correct subject at call sites.

    Parallel to SecurityEvent; does NOT extend it.
    """

    op: Literal["put", "get", "exists", "delete"]
    """Which BlobStore operation was performed."""

    result: Literal["ok", "not_found", "unauthorized", "write_failed", "internal_error"]
    """Outcome of the operation."""

    store_key: str | None
    """Blob handle returned by the store; None for unauthorized requests."""

    content_hash: str | None
    """SHA-256 hex digest of the blob; None for unauthorized or put-failures
    that occurred before the hash was computed."""

    size: int | None
    """Blob size in bytes; None when the blob was not read."""

    source: str | None
    """Value of the X-Blob-Source request header; None for non-PUT operations."""

    kind: Literal["blobs.op"] = "blobs.op"
    """Event kind discriminator — always 'blobs.op' for V8."""

    subject: str = Field(
        default="service:lyra-blobstore",
        description=(
            "Phase 1: 'service:lyra-blobstore'. Phase 2 (#1334): 'agent/<id>' "
            "once per-identity tokens land. Migration anchor for the audit consumer."
        ),
    )
    """Caller identity — phase 1 fixed service identity, phase 2 per-agent (#1334)."""

    @classmethod
    def subject_for(cls, op: str) -> str:
        """Return the NATS subject for the given blob operation.

        Examples::

            BlobAuditEvent.subject_for("put")    # "lyra.audit.blobs.put"
            BlobAuditEvent.subject_for("delete")  # "lyra.audit.blobs.delete"
        """
        return f"lyra.audit.blobs.{op}"
