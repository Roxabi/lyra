"""Span attribute registry SSoT — see artifacts/specs/otel-span-attributes-spec.mdx."""

from __future__ import annotations

ATTR_TRACE_ID = "roxabi.trace_id"
ATTR_JOB_ID = "roxabi.job_id"
ATTR_PARENT_JOB_ID = "roxabi.parent_job_id"
ATTR_POOL_ID = "roxabi.pool_id"
ATTR_COMPONENT = "roxabi.component"
ATTR_ENVELOPE_NAME = "roxabi.envelope_name"
ATTR_SUBJECT = "roxabi.subject"
ATTR_MODEL = "roxabi.model"
ATTR_SKILL = "roxabi.skill"
ATTR_ENGINE = "roxabi.engine"
ATTR_BLOB_REF_IN = "roxabi.blob_ref.in"
ATTR_BLOB_REF_OUT = "roxabi.blob_ref.out"
ATTR_RUNTIME = "roxabi.runtime"

ATTR_SKILL_UNKNOWN = "unknown"

FORBIDDEN_ATTRS: frozenset[str] = frozenset(
    {
        "prompt",
        "messages",
        "text",
        "audio_b64",
        "image_b64",
        "waveform_b64",
        "audio_bytes",
        "file_path",
        "api_key",
        "token",
        "secret",
    }
)

FORBIDDEN_ATTR_PREFIXES: tuple[str, ...] = (
    "user.",
    "home.",
)