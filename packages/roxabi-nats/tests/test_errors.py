"""Tests for sanitize_for_wire — socket-bound exception sanitization.

Covers the contract enforced by ``roxabi_nats.errors.sanitize_for_wire``:
str-cast + URL credential scrub (delegated to
``roxabi_contracts.errors.scrub_credentials``) + bounded-length truncation
(delegated to ``truncate_with_marker``).
"""

from __future__ import annotations

import pytest

from roxabi_nats import sanitize_for_wire
from roxabi_nats.errors import DEFAULT_MAX_LEN


class TestStrCast:
    def test_plain_message_passes_through(self) -> None:
        exc = RuntimeError("worker crashed")
        assert sanitize_for_wire(exc) == "worker crashed"

    def test_empty_message(self) -> None:
        exc = RuntimeError("")
        assert sanitize_for_wire(exc) == ""

    def test_works_on_base_exception_subtypes(self) -> None:
        # BaseException covers SystemExit / KeyboardInterrupt / GeneratorExit;
        # caller decides whether to call us on those. We must not refuse them.
        for exc in (
            ValueError("bad input"),
            KeyError("missing"),
            SystemExit("shutting down"),
            KeyboardInterrupt(),
        ):
            result = sanitize_for_wire(exc)
            assert isinstance(result, str)

    def test_custom_exception_str(self) -> None:
        class MyError(Exception):
            def __str__(self) -> str:
                return "custom repr"

        assert sanitize_for_wire(MyError()) == "custom repr"


class TestCredentialScrub:
    def test_nats_url_userinfo_scrubbed(self) -> None:
        exc = RuntimeError("connect failed: nats://admin:s3cret@broker:4222")
        assert "admin:s3cret" not in sanitize_for_wire(exc)
        assert "***:***@broker:4222" in sanitize_for_wire(exc)

    def test_postgres_url_userinfo_scrubbed(self) -> None:
        exc = RuntimeError("pg error: postgres://u:p@db.host/mydb")
        assert "u:p" not in sanitize_for_wire(exc)
        assert "***:***@db.host" in sanitize_for_wire(exc)

    @pytest.mark.parametrize(
        "scheme",
        ["nats", "nats+tls", "redis", "rediss", "amqp", "amqps", "http", "https"],
    )
    def test_allowlisted_scheme_scrubbed(self, scheme: str) -> None:
        exc = RuntimeError(f"err: {scheme}://user:pass@host/path")
        assert "user:pass" not in sanitize_for_wire(exc)

    def test_unknown_scheme_unchanged(self) -> None:
        # custom:// is not in the credential scheme allowlist
        exc = RuntimeError("err: custom://user:pass@host")
        assert "user:pass" in sanitize_for_wire(exc)

    def test_url_without_userinfo_unchanged(self) -> None:
        exc = RuntimeError("err: nats://broker:4222")
        assert sanitize_for_wire(exc) == "err: nats://broker:4222"

    def test_multiple_urls_all_scrubbed(self) -> None:
        exc = RuntimeError(
            "primary nats://a:1@x and fallback postgres://b:2@y both down"
        )
        result = sanitize_for_wire(exc)
        assert "a:1" not in result
        assert "b:2" not in result
        assert "***:***@x" in result
        assert "***:***@y" in result


class TestTruncation:
    def test_below_limit_unchanged(self) -> None:
        msg = "short"
        assert sanitize_for_wire(RuntimeError(msg), max_len=100) == msg

    def test_exact_limit_unchanged(self) -> None:
        msg = "x" * 50
        assert sanitize_for_wire(RuntimeError(msg), max_len=50) == msg

    def test_overflow_truncated_with_marker(self) -> None:
        msg = "y" * 500
        result = sanitize_for_wire(RuntimeError(msg), max_len=100)
        assert len(result) == 100
        assert result.endswith("…")

    def test_default_max_len_applied(self) -> None:
        msg = "z" * (DEFAULT_MAX_LEN + 100)
        result = sanitize_for_wire(RuntimeError(msg))
        assert len(result) == DEFAULT_MAX_LEN
        assert result.endswith("…")

    def test_default_max_len_is_200(self) -> None:
        # Pinned for ADR/contract clarity — if the default ever moves,
        # update consumers + this test in the same change.
        assert DEFAULT_MAX_LEN == 200


class TestScrubBeforeTruncate:
    def test_scrub_then_truncate_order(self) -> None:
        # Scrub first: a long URL with credentials must have its userinfo
        # replaced before truncation, so secrets cannot survive by virtue
        # of being past the cutoff.
        url = "nats://verylonguser:verylongpassword@broker.example.com:4222/path"
        msg = f"failure connecting: {url}"
        result = sanitize_for_wire(RuntimeError(msg), max_len=80)
        assert "verylonguser" not in result
        assert "verylongpassword" not in result
        assert len(result) <= 80
