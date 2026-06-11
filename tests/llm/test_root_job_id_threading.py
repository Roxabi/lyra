"""SC-7 — root_job_id end-to-end threading test (#1620, ADR-084).

Asserts that the root_job_id minted at adapter ingress threads through to:
  1. LlmRequest.job_id via CliNatsCodec.encode(root_job_id=...)
  2. TurnWriteEvent.job_id via PoolObserver.append() → TurnPublisher.publish_log_turn()

Pure unit tests: no NATS, no network, no sleeps.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.core.agent.agent_config import ModelConfig
from factory.core.auth.trust import TrustLevel
from factory.core.messaging.message import InboundMessage, TelegramMeta
from factory.core.pool.pool_observer import PoolObserver
from factory.llm.cli_nats_codec import CliNatsCodec
from roxabi_contracts import new_job_id
from roxabi_contracts.turns import TurnWriteEvent

_MODEL = ModelConfig(backend="nats", model="claude-sonnet-4-6")
_SESSION_ID = "sess-sc7-test"
_POOL_ID = "pool:tg:chat:42"


def _make_inbound_message_with_root_job_id(root_job_id: str) -> InboundMessage:
    """Construct InboundMessage directly with an explicit root_job_id."""
    return InboundMessage(
        id="msg-sc7-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id="tg:user:99",
        user_name="SC7Tester",
        is_mention=False,
        text="hello sc7",
        text_raw="hello sc7",
        timestamp=datetime.now(timezone.utc),
        platform_meta=TelegramMeta(chat_id=42),
        trust_level=TrustLevel.TRUSTED,
        root_job_id=root_job_id,
    )


class TestCliNatsCodecRootJobIdThreading:
    """SC-7 / codec leg: adapter receipt → LlmRequest.job_id == minted root."""

    def test_encode_with_root_job_id_propagates_to_llm_request(self) -> None:
        minted = new_job_id()
        codec = CliNatsCodec()
        payload, _ = codec.encode(
            "hello",
            _MODEL,
            "sys",
            None,
            stream=False,
            root_job_id=minted,
        )
        body = json.loads(payload)
        assert body["job_id"] == minted

    def test_encode_without_root_job_id_mints_fresh_job_id(self) -> None:
        """Backward-compat: omitting root_job_id still produces a valid job_id."""
        codec = CliNatsCodec()
        payload, _ = codec.encode("hello", _MODEL, "sys", None, stream=False)
        body = json.loads(payload)
        assert isinstance(body["job_id"], str) and len(body["job_id"]) > 0

    def test_encode_with_none_root_job_id_mints_fresh_job_id(self) -> None:
        """Explicit None falls back to new_job_id()."""
        codec = CliNatsCodec()
        payload, _ = codec.encode(
            "hello", _MODEL, "sys", None, stream=False, root_job_id=None
        )
        body = json.loads(payload)
        assert isinstance(body["job_id"], str) and len(body["job_id"]) > 0

    def test_encode_two_calls_with_same_root_job_id_share_job_id(self) -> None:
        """root_job_id is threaded, not re-minted on every encode call."""
        minted = new_job_id()
        codec = CliNatsCodec()
        payload1, _ = codec.encode(
            "turn 1", _MODEL, "sys", None, stream=False, root_job_id=minted
        )
        payload2, _ = codec.encode(
            "turn 2", _MODEL, "sys", None, stream=False, root_job_id=minted
        )
        assert json.loads(payload1)["job_id"] == minted
        assert json.loads(payload2)["job_id"] == minted

    def test_encode_trace_id_is_independent_of_root_job_id(self) -> None:
        """trace_id is always a fresh UUID; root_job_id does not replace it."""
        minted = new_job_id()
        codec = CliNatsCodec()
        payload, trace_id = codec.encode(
            "hello", _MODEL, "sys", None, stream=False, root_job_id=minted
        )
        body = json.loads(payload)
        assert body["job_id"] == minted
        assert body["trace_id"] == trace_id
        assert body["trace_id"] != minted


class TestPoolObserverRootJobIdThreading:
    """SC-7 / observer leg: InboundMessage.root_job_id → TurnWriteEvent.job_id."""

    @pytest.mark.anyio
    async def test_append_propagates_root_job_id_to_publish_log_turn(self) -> None:
        """append() with root_job_id-carrying message passes it to publish_log_turn."""
        minted = new_job_id()
        msg = _make_inbound_message_with_root_job_id(minted)

        mock_js = MagicMock()
        mock_js.publish = AsyncMock(return_value=MagicMock())

        from factory.transport.turn_publisher import TurnPublisher

        publisher = TurnPublisher(mock_js)

        obs = PoolObserver(pool_id=_POOL_ID, session_id_fn=lambda: _SESSION_ID)
        obs.register_turn_publisher(publisher)

        await obs.append(msg, session_id=_SESSION_ID)

        mock_js.publish.assert_called_once()
        call_args = mock_js.publish.call_args
        _, raw = call_args[0]
        event = TurnWriteEvent.model_validate(json.loads(raw.decode()))
        assert event.job_id == minted

    @pytest.mark.anyio
    async def test_append_without_root_job_id_still_publishes_a_job_id(self) -> None:
        """Backward-compat: message without root_job_id produces a fresh job_id."""
        msg = InboundMessage(
            id="msg-sc7-back",
            platform="telegram",
            bot_id="main",
            scope_id="chat:42",
            user_id="tg:user:99",
            user_name="SC7Tester",
            is_mention=False,
            text="hello back",
            text_raw="hello back",
            timestamp=datetime.now(timezone.utc),
            platform_meta=TelegramMeta(chat_id=42),
            trust_level=TrustLevel.TRUSTED,
            # root_job_id omitted → None by default
        )

        mock_js = MagicMock()
        mock_js.publish = AsyncMock(return_value=MagicMock())

        from factory.transport.turn_publisher import TurnPublisher

        publisher = TurnPublisher(mock_js)
        obs = PoolObserver(pool_id=_POOL_ID, session_id_fn=lambda: _SESSION_ID)
        obs.register_turn_publisher(publisher)

        await obs.append(msg, session_id=_SESSION_ID)

        mock_js.publish.assert_called_once()
        call_args = mock_js.publish.call_args
        _, raw = call_args[0]
        event = TurnWriteEvent.model_validate(json.loads(raw.decode()))
        # Falls back to new_job_id() — must be a non-empty string
        assert isinstance(event.job_id, str) and len(event.job_id) > 0
