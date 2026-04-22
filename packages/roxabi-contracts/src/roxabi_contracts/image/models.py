"""Image-domain NATS contract models.

Pure Pydantic. No NATS imports. No transport logic. Every model subclasses
ContractEnvelope, which provides (contract_version, trace_id, issued_at)
plus ConfigDict(extra="ignore") for forward-compat.

Mirrors the voice-domain shape. See spec #763 and issue #806 for the
alignment rationale on optional-but-invariant fields on response models.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import StringConstraints, model_validator

from roxabi_contracts.envelope import ContractEnvelope


class ImageRequest(ContractEnvelope):
    """Image generation request. Canonical subject: ``lyra.image.generate.request``."""

    request_id: Annotated[str, StringConstraints(min_length=1)]
    prompt: Annotated[str, StringConstraints(min_length=1)]
    engine: str
    negative_prompt: str | None = None
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    guidance: float | None = None
    seed: int | None = None
    format: Literal["png", "jpeg", "webp"] = "png"
    output_mode: Literal["b64", "file"] = "b64"
    lora_path: str | None = None
    lora_scale: float | None = None
    trigger: str | None = None
    embedding_path: str | None = None


class ImageResponse(ContractEnvelope):
    """Image generation response.

    Success-path invariant (enforced by ``_enforce_success_invariant``):
    when ``ok=True``, exactly one of ``image_b64`` / ``file_path`` is
    non-null, AND ``mime_type`` AND ``width`` AND ``height`` AND
    ``engine`` AND ``seed_used`` are all non-null. Error-path
    (``ok=False``) omits them and sets ``error``.
    """

    ok: bool
    request_id: Annotated[str, StringConstraints(min_length=1)]
    image_b64: str | None = None
    file_path: str | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    engine: str | None = None
    seed_used: int | None = None
    error: str | None = None
    error_detail: str | None = None

    @model_validator(mode="after")
    def _enforce_success_invariant(self) -> Self:
        if not self.ok:
            return self
        payload_set = (self.image_b64 is not None) ^ (self.file_path is not None)
        if not payload_set:
            raise ValueError(
                "ImageResponse with ok=True must carry exactly one of "
                "image_b64 / file_path (see issue #806)"
            )
        if (
            self.mime_type is None
            or self.width is None
            or self.height is None
            or self.engine is None
            or self.seed_used is None
        ):
            raise ValueError(
                "ImageResponse with ok=True must carry mime_type, width, "
                "height, engine, and seed_used (see issue #806)"
            )
        return self


class ImageHeartbeat(ContractEnvelope):
    """Inbound heartbeat from an image worker satellite.

    Canonical subject: ``lyra.image.heartbeat``. Consumers populate a
    worker registry keyed by ``worker_id`` and prune stale entries via
    ``ts``.
    """

    worker_id: Annotated[str, StringConstraints(min_length=1)]
    service: str
    host: str
    subject: str
    queue_group: str
    ts: float
    engine_loaded: str | None = None
    active_requests: int | None = None
    vram_used_mb: int | None = None
    vram_total_mb: int | None = None
