"""Tests for sanitize_for_wire — socket-bound exception sanitization.

Covers the contract enforced by ``roxabi_nats.errors.sanitize_for_wire``:
str-cast + URL credential scrub (delegated to
``roxabi_contracts.errors.scrub_credentials``) + bounded-length truncation
(delegated to ``truncate_with_marker``).
"""

from __future__ import annotations

import pytest

from roxabi_nats import DEFAULT_MAX_LEN, sanitize_for_wire


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
    @pytest.mark.parametrize(
        "scheme",
        [
            "nats",
            "nats+tls",
            "redis",
            "rediss",
            "amqp",
            "amqps",
            "http",
            "https",
            "postgres",
            "postgresql",
            "mysql",
        ],
    )
    def test_allowlisted_scheme_userinfo_scrubbed(self, scheme: str) -> None:
        exc = RuntimeError(f"err: {scheme}://user:pass@host/path")
        result = sanitize_for_wire(exc)
        assert "user:pass" not in result
        assert "***:***@host" in result

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

    def test_ipv6_host_userinfo_scrubbed(self) -> None:
        # Bracketed IPv6 host is a common regex blind spot. Delegating to
        # urlsplit (RFC 3986) handles it correctly; pin the behavior so a
        # future regex change cannot regress silently.
        exc = RuntimeError("connect: nats://user:pass@[::1]:4222")
        result = sanitize_for_wire(exc)
        assert "user:pass" not in result
        assert "***:***@[::1]:4222" in result

    def test_password_with_percent_encoded_at(self) -> None:
        exc = RuntimeError("connect: nats://user:p%40ss@host:4222")
        result = sanitize_for_wire(exc)
        assert "p%40ss" not in result
        assert "***:***@host:4222" in result

    def test_password_with_raw_at(self) -> None:
        # RFC 3986 anchors userinfo on the LAST `@` before the host,
        # so a raw `@` inside the password must still be scrubbed.
        exc = RuntimeError("connect: nats://user:p@ss@host:4222")
        result = sanitize_for_wire(exc)
        assert "user:p@ss" not in result
        assert "***:***@host:4222" in result


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

    def test_overflow_at_marker_length_returns_marker_only(self) -> None:
        # max_len=1 is the smallest legal limit (marker length).
        # An overflowing input must reduce to just the marker.
        result = sanitize_for_wire(RuntimeError("hello"), max_len=1)
        assert result == "…"

    def test_default_max_len_applied(self) -> None:
        msg = "z" * (DEFAULT_MAX_LEN + 100)
        result = sanitize_for_wire(RuntimeError(msg))
        assert len(result) == DEFAULT_MAX_LEN
        assert result.endswith("…")

    def test_default_max_len_contract_value_200(self) -> None:
        # Pinned by contract — worker repos consume `sanitize_for_wire` from
        # `roxabi-nats` and may size their own buffers to this value. Moving
        # the default is a contract change: bump it deliberately and update
        # consumers in lockstep.
        assert DEFAULT_MAX_LEN == 200

    @pytest.mark.parametrize("max_len", [0, -1, -100])
    def test_max_len_below_marker_raises(self, max_len: int) -> None:
        # Below the marker length (1) the truncate primitive cannot produce
        # a bounded string. Refuse loudly rather than return an oversized
        # result that violates the caller's max_len contract.
        with pytest.raises(ValueError):
            sanitize_for_wire(RuntimeError("hello"), max_len=max_len)


class TestScrubBeforeTruncate:
    def test_scrub_runs_before_truncate(self) -> None:
        # Pipeline order matters: scrub MUST run before truncate. Otherwise
        # truncation could cut the URL between the credential and the `@host`
        # anchor, and scrub (which keys off `@`) would leave the partial
        # credential exposed.
        #
        # With max_len=35:
        #   scrub-first:    "connect_error: nats://***:***@brok…"  (credentials gone)
        #   truncate-first: "connect_error: nats://canary_user:…"  (no @, scrub no-ops)
        #
        # If the pipeline order ever flips, the canary_user assertion fails.
        msg = "connect_error: nats://canary_user:canary_pass@broker:4222"
        result = sanitize_for_wire(RuntimeError(msg), max_len=35)
        assert "canary_user" not in result
        assert "canary_pass" not in result
        assert result.endswith("…")
