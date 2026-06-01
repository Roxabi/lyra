"""RED-phase tests for roxabi_contracts.errors.WorkerError + KNOWN_CODES.

All tests will fail with ImportError until T4 (backend-dev-A) ships
packages/roxabi-contracts/src/roxabi_contracts/errors.py.

Spec trace: SC-C1 / N1, N2 / S1
Plan task: T1
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from roxabi_contracts.errors import KNOWN_CODES, CodeMeta, WorkerError

# ---------------------------------------------------------------------------
# Construction + defaults
# ---------------------------------------------------------------------------


def test_worker_error_construction_minimal() -> None:
    """Required fields code + message succeed; defaults apply."""
    # Arrange / Act
    err = WorkerError(code="worker.crash", message="Something exploded")

    # Assert
    assert err.code == "worker.crash"
    assert err.message == "Something exploded"
    assert err.retryable is True
    assert err.detail is None


def test_worker_error_retryable_false_accepted() -> None:
    """retryable=False overrides the default."""
    # Arrange / Act
    err = WorkerError(code="cli.parse", message="bad parse", retryable=False)

    # Assert
    assert err.retryable is False


def test_worker_error_detail_accepted() -> None:
    """detail field is stored when provided."""
    # Arrange / Act
    err = WorkerError(code="llm.rate_limit", message="rate limited", detail="quota=100")

    # Assert
    assert err.detail == "quota=100"


# ---------------------------------------------------------------------------
# Validation: code must not be empty
# ---------------------------------------------------------------------------


def test_worker_error_code_empty_raises() -> None:
    """Empty code string raises ValidationError (min_length=1)."""
    # Arrange / Act / Assert
    with pytest.raises(ValidationError):
        WorkerError(code="", message="some message")


# ---------------------------------------------------------------------------
# Validation: message must not be empty
# ---------------------------------------------------------------------------


def test_worker_error_message_empty_raises() -> None:
    """Empty message string raises ValidationError (min_length=1)."""
    # Arrange / Act / Assert
    with pytest.raises(ValidationError):
        WorkerError(code="worker.crash", message="")


# ---------------------------------------------------------------------------
# Validation: detail max_length=2048
# ---------------------------------------------------------------------------


def test_worker_error_detail_exact_2048_accepted() -> None:
    """detail of exactly 2048 characters is accepted (boundary value)."""
    # Arrange
    detail_2048 = "x" * 2048

    # Act
    err = WorkerError(code="worker.internal", message="ctx", detail=detail_2048)

    # Assert
    assert len(err.detail) == 2048  # type: ignore[arg-type]


def test_worker_error_detail_2049_truncated_with_marker() -> None:
    """detail of 2049 characters is truncated to 2048 with a `…` suffix.

    Truncation (not rejection) is required because WorkerError construction
    sites live inside `except` handlers — raising on overflow would crash
    the very error path that exists to surface the problem.
    """
    # Arrange
    detail_2049 = "x" * 2049

    # Act
    err = WorkerError(code="worker.internal", message="ctx", detail=detail_2049)

    # Assert
    assert len(err.detail) == 2048  # type: ignore[arg-type]
    assert err.detail.endswith("…")  # type: ignore[union-attr]


def test_worker_error_message_truncated_with_marker() -> None:
    """message longer than 512 chars is truncated to 512 with a `…` suffix."""
    # Arrange
    long_message = "x" * 1000

    # Act — must not raise
    err = WorkerError(code="worker.crash", message=long_message)

    # Assert
    assert len(err.message) == 512
    assert err.message.endswith("…")


def test_worker_error_detail_none_accepted() -> None:
    """detail=None is the explicit default and is accepted."""
    # Arrange / Act
    err = WorkerError(code="transport.timeout", message="timed out", detail=None)

    # Assert
    assert err.detail is None


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------


def test_worker_error_json_roundtrip() -> None:
    """WorkerError serialises + deserialises to an equal instance."""
    # Arrange
    original = WorkerError(
        code="transport.parse",
        message="malformed payload",
        retryable=False,
        detail="raw={}",
    )

    # Act
    json_str = original.model_dump_json()
    restored = WorkerError.model_validate_json(json_str)

    # Assert
    assert restored == original
    assert restored.code == "transport.parse"
    assert restored.retryable is False
    assert restored.detail == "raw={}"


# ---------------------------------------------------------------------------
# Forward-compat: extra fields ignored
# ---------------------------------------------------------------------------


def test_worker_error_extra_fields_ignored() -> None:
    """Unknown fields in JSON are silently ignored (extra='ignore')."""
    # Arrange
    json_with_extra = (
        '{"code": "worker.crash", "message": "boom", '
        '"retryable": true, "unknown_future_field": "ignored"}'
    )

    # Act — must not raise
    err = WorkerError.model_validate_json(json_with_extra)

    # Assert
    assert err.code == "worker.crash"
    assert not hasattr(err, "unknown_future_field")


# ---------------------------------------------------------------------------
# KNOWN_CODES: shape
# ---------------------------------------------------------------------------


def test_known_codes_is_dict() -> None:
    """KNOWN_CODES is a dict."""
    # Assert
    assert isinstance(KNOWN_CODES, dict)


def test_known_codes_values_are_code_meta() -> None:
    """Every value in KNOWN_CODES is a CodeMeta instance."""
    # Assert
    for code, meta in KNOWN_CODES.items():
        assert isinstance(meta, CodeMeta), f"KNOWN_CODES[{code!r}] is not a CodeMeta"


def test_code_meta_has_required_fields() -> None:
    """Each CodeMeta has domain: str, default_retryable: bool, description: str."""
    # Assert
    for code, meta in KNOWN_CODES.items():
        assert isinstance(meta.domain, str), f"{code}: domain must be str"
        assert isinstance(meta.default_retryable, bool), (
            f"{code}: default_retryable must be bool"
        )
        assert isinstance(meta.description, str), f"{code}: description must be str"
        assert meta.domain, f"{code}: domain must not be empty"
        assert meta.description, f"{code}: description must not be empty"


# ---------------------------------------------------------------------------
# KNOWN_CODES: sample presence assertions (from ADR-066 (absorbed into ADR-049)
# §"The code namespace")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        "transport.timeout",
        "transport.no_responders",
        "transport.parse",
        "transport.contract_mismatch",
        "transport.slow_consumer",
        "transport.error",
        "worker.crash",
        "worker.validation",
        "worker.internal",
        "worker.capacity",
        "worker.busy",
        "cli.auth",
        "cli.session_lost",
        "cli.parse",
        "llm.rate_limit",
        "llm.context_too_long",
        "llm.model_unavailable",
        "llm.no_responders",
        "voice.engine_unavailable",
        "voice.audio_invalid",
        "image.engine_unavailable",
        "image.prompt_rejected",
    ],
)
def test_known_codes_contains_adr_codes(code: str) -> None:
    """Every code listed in ADR-066 (absorbed into ADR-049)
    §'The code namespace' is present."""
    assert code in KNOWN_CODES, f"Missing expected code: {code!r}"


# ---------------------------------------------------------------------------
# Code regex — forge-safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_code",
    [
        "CLI.AUTH",
        "Worker.Crash",
        "9invalid",
        "with\nnewline",
        "with\ttab",
        "with space",
        ".leading.dot",
        "with/slash",
        "with:colon",
        "with@at",
    ],
    ids=[
        "uppercase",
        "mixed-case",
        "leading-digit",
        "newline",
        "tab",
        "space",
        "leading-dot",
        "slash",
        "colon",
        "at-sign",
    ],
)
def test_worker_error_code_pattern_rejects(bad_code: str) -> None:
    """Code must match `^[a-z][a-z0-9._-]*$` — newlines / control chars rejected."""
    with pytest.raises(ValidationError):
        WorkerError(code=bad_code, message="x")


# ---------------------------------------------------------------------------
# Credential scrubber
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "Connection failed: nats://user:pass@host:4222",
            "Connection failed: nats://***:***@host:4222",
        ),
        (
            # Password contains @ — urlsplit anchors on the LAST @ before host
            "nats://user:p@ssword@host:4222",
            "nats://***:***@host:4222",
        ),
        (
            "Multiple URLs: nats://a:b@h1 and amqp://c:d@h2",
            "Multiple URLs: nats://***:***@h1 and amqp://***:***@h2",
        ),
        (
            # URL-encoded user
            "nats://user%40domain:pass@host",
            "nats://***:***@host",
        ),
        (
            # Empty password — `user:@host` form
            "nats://user:@host",
            "nats://***:***@host",
        ),
        (
            # Postgres + Redis
            "Bad URL postgresql://u:p@db:5432/x and rediss://u:p@cache",
            "Bad URL postgresql://***:***@db:5432/x and rediss://***:***@cache",
        ),
    ],
    ids=[
        "nats-basic",
        "at-in-password",
        "multi-url",
        "url-encoded-user",
        "empty-password",
        "postgres-and-redis",
    ],
)
def test_worker_error_message_scrubs_credentials(raw: str, expected: str) -> None:
    """Credential userinfo in known-scheme URLs is replaced with `***:***`."""
    err = WorkerError(code="worker.crash", message=raw)
    assert err.message == expected


def test_worker_error_detail_scrubs_credentials() -> None:
    """detail field is also scrubbed via the same validator."""
    err = WorkerError(
        code="worker.crash",
        message="boom",
        detail="raw=nats://u:p@host",
    )
    assert err.detail == "raw=nats://***:***@host"


def test_worker_error_unknown_scheme_not_scrubbed() -> None:
    """URLs whose schemes are not in the credential allowlist are left alone."""
    err = WorkerError(
        code="worker.crash",
        message="ftp://user:pass@host left alone",
    )
    assert err.message == "ftp://user:pass@host left alone"
