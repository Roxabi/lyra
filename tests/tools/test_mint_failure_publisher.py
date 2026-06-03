"""Tests for factory.tools.gh_token.mint_failure_publisher.

Covers:
- publish() builds and sends MintFailureEvent with correct subject/payload
- reason_label mapping (http_status → "github_api_<N>", None → "network_error")
- publish() swallows NATS errors — best-effort, never raises
"""

from __future__ import annotations

from factory.tools.gh_token.helper import MintError
from factory.tools.gh_token.mint_failure_publisher import MintFailurePublisher
from roxabi_contracts.gh.models import MintFailureEvent
from roxabi_contracts.gh.subjects import gh_mint_failure

# ── fake NC ───────────────────────────────────────────────────────────────────


class FakeNC:
    """Minimal NATS client stub that records publish() calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes]] = []

    async def publish(self, subject: str, data: bytes) -> None:
        self.calls.append((subject, data))


class BrokenNC:
    """NATS client stub whose publish() always raises."""

    async def publish(self, _subject: str, _data: bytes) -> None:
        raise RuntimeError("down")


# ── T9.1 — HTTP error case ────────────────────────────────────────────────────


async def test_publish_http_error_sends_event() -> None:
    """MintError with http_status=401 → correct subject, valid MintFailureEvent payload.

    Verifies:
    - exactly one publish call is made
    - subject == gh_mint_failure("testhost") and starts with "factory.gh.mint_failure."
    - deserialized event has reason="github_api_401", http_status=401,
      machine="testhost", retries=0
    """
    # Arrange
    fake_nc = FakeNC()
    publisher = MintFailurePublisher(fake_nc, "testhost")  # type: ignore[arg-type]
    exc = MintError(reason="boom", http_status=401, retries=0)

    # Act
    await publisher.publish(exc)

    # Assert — exactly one call
    assert len(fake_nc.calls) == 1

    subject, data = fake_nc.calls[0]

    # Subject correctness
    expected_subject = gh_mint_failure("testhost")
    assert subject == expected_subject

    # Payload correctness
    event = MintFailureEvent.model_validate_json(data)
    assert event.reason == "github_api_401"
    assert event.http_status == 401
    assert event.machine == "testhost"
    assert event.retries == 0


# ── T9.2 — Network error case ─────────────────────────────────────────────────


async def test_publish_network_error_sends_event() -> None:
    """MintError with http_status=None → reason='network_error', http_status is None.

    Network/transport failures have no HTTP response; the event must carry
    "network_error" as reason and None for http_status.
    """
    # Arrange
    fake_nc = FakeNC()
    publisher = MintFailurePublisher(fake_nc, "testhost")  # type: ignore[arg-type]
    exc = MintError(reason="net", http_status=None)

    # Act
    await publisher.publish(exc)

    # Assert — exactly one call
    assert len(fake_nc.calls) == 1

    _subject, data = fake_nc.calls[0]
    event = MintFailureEvent.model_validate_json(data)
    assert event.reason == "network_error"
    assert event.http_status is None


# ── T9.3 — Swallow NATS errors ────────────────────────────────────────────────


async def test_publish_swallows_nats_error() -> None:
    """publish() must not raise even when the NATS client raises RuntimeError.

    Deleting the try/except in publish() would cause this test to fail —
    the RuntimeError from BrokenNC.publish() would propagate out of the method.
    """
    # Arrange
    broken_nc = BrokenNC()
    publisher = MintFailurePublisher(broken_nc, "testhost")  # type: ignore[arg-type]
    exc = MintError(reason="any", http_status=500, retries=1)

    # Act + Assert — must return normally (no exception)
    await publisher.publish(exc)
