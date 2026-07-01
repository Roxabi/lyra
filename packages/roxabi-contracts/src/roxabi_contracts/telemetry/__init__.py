"""Telemetry contracts — hooks protocol and span attribute registry (#2069)."""

from .attrs import (
    ATTR_BLOB_REF_IN,
    ATTR_BLOB_REF_OUT,
    ATTR_COMPONENT,
    ATTR_ENGINE,
    ATTR_ENVELOPE_NAME,
    ATTR_JOB_ID,
    ATTR_MODEL,
    ATTR_PARENT_JOB_ID,
    ATTR_POOL_ID,
    ATTR_RUNTIME,
    ATTR_SKILL,
    ATTR_SKILL_UNKNOWN,
    ATTR_SUBJECT,
    ATTR_TRACE_ID,
    FORBIDDEN_ATTR_PREFIXES,
    FORBIDDEN_ATTRS,
)
from .hooks import MessageLifecycleHooks

__all__ = [
    "ATTR_BLOB_REF_IN",
    "ATTR_BLOB_REF_OUT",
    "ATTR_COMPONENT",
    "ATTR_ENGINE",
    "ATTR_ENVELOPE_NAME",
    "ATTR_JOB_ID",
    "ATTR_MODEL",
    "ATTR_PARENT_JOB_ID",
    "ATTR_POOL_ID",
    "ATTR_RUNTIME",
    "ATTR_SKILL",
    "ATTR_SKILL_UNKNOWN",
    "ATTR_SUBJECT",
    "ATTR_TRACE_ID",
    "FORBIDDEN_ATTR_PREFIXES",
    "FORBIDDEN_ATTRS",
    "MessageLifecycleHooks",
]