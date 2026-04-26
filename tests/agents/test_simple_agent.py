"""Tests for lyra.agents.simple_agent: extract_text and SimpleAgent.process."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

if TYPE_CHECKING:
    from lyra.core.cli.cli_pool import CliPool
    from lyra.llm.base import LlmProvider

from lyra.agents.simple_agent import SimpleAgent
from lyra.core.agent import Agent
from lyra.core.agent.agent_config import ModelConfig
from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import (
    InboundMessage,
    Response,
)
from lyra.core.pool import Pool
from lyra.llm.base import LlmResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_inbound_message(text: str = "hello") -> InboundMessage:
    return InboundMessage(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id="alice",
        user_name="Alice",
        is_mention=False,
        text=text,
        text_raw=text,
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 42,
            "topic_id": None,
            "message_id": None,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
    )


def make_pool(pool_id: str = "telegram:main:alice") -> Pool:
    return Pool(pool_id=pool_id, agent_name="lyra", ctx=MagicMock())


def make_agent(provider: object) -> SimpleAgent:
    config = Agent(
        name="lyra",
        system_prompt="You are Lyra.",
        memory_namespace="lyra",
        llm_config=ModelConfig(),
    )
    return SimpleAgent(config, cast("LlmProvider", provider))


# ---------------------------------------------------------------------------
# TestSimpleAgentProcess
# ---------------------------------------------------------------------------


class TestSimpleAgentProcess:
    async def test_success_response(self) -> None:
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(result="hello", session_id="s1")
        )
        agent = make_agent(provider)
        msg = make_inbound_message("hi")
        pool = make_pool()

        response = await agent.process(msg, pool)

        assert isinstance(response, Response)
        assert response.content == "hello"
        assert response.metadata["session_id"] == "s1"
        assert "error" not in response.metadata

    async def test_error_response(self) -> None:
        provider = MagicMock()
        provider.complete = AsyncMock(return_value=LlmResult(error="boom"))
        agent = make_agent(provider)
        msg = make_inbound_message("hi")
        pool = make_pool()

        response = await agent.process(msg, pool)

        # Internal detail must NOT leak to the user
        assert isinstance(response, Response)
        assert "boom" not in response.content
        assert response.content == "Something went wrong. Please try again."
        assert response.metadata.get("error") is True

    async def test_timeout_error_response(self) -> None:
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(error="Timeout after 300s")
        )
        agent = make_agent(provider)
        msg = make_inbound_message("hi")
        pool = make_pool()

        response = await agent.process(msg, pool)

        assert isinstance(response, Response)
        assert response.content == "Your request timed out. Please try again."
        assert response.metadata.get("error") is True

    async def test_warning_response(self) -> None:
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(result="ok", session_id="s1", warning="truncated")
        )
        agent = make_agent(provider)
        msg = make_inbound_message("hi")
        pool = make_pool()

        response = await agent.process(msg, pool)

        assert isinstance(response, Response)
        assert response.content == "ok"
        assert response.metadata["warning"] == "truncated"
        assert response.metadata["session_id"] == "s1"

    async def test_send_called_with_pool_id_and_text(self) -> None:
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(result="ok", session_id="s1")
        )
        agent = make_agent(provider)
        msg = make_inbound_message("test text")
        pool = make_pool(pool_id="telegram:main:bob")

        await agent.process(msg, pool)

        provider.complete.assert_awaited_once()
        args = provider.complete.call_args
        assert args[0][0] == "telegram:main:bob"
        assert args[0][1] == "<user_message>test text</user_message>"

    async def test_processor_enriched_msg_not_double_wrapped(self) -> None:
        """processor_enriched=True: text with <webpage> tags is passed verbatim."""
        import dataclasses

        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(result="ok", session_id="s1")
        )
        agent = make_agent(provider)
        enriched_text = "<webpage>some scraped content</webpage>"
        msg = dataclasses.replace(
            make_inbound_message(enriched_text),
            processor_enriched=True,
        )
        pool = make_pool()

        await agent.process(msg, pool)

        provider.complete.assert_awaited_once()
        args = provider.complete.call_args
        sent_text = args[0][1]
        # Must NOT wrap in <user_message>...</user_message>
        assert "<user_message>" not in sent_text
        assert sent_text == enriched_text


# ---------------------------------------------------------------------------
# TestSimpleAgentStreaming
# ---------------------------------------------------------------------------


async def _fake_async_gen(*chunks: str) -> AsyncIterator[str]:
    """Return an async iterator yielding the given chunks."""
    for chunk in chunks:
        yield chunk


def make_streaming_agent(provider: object, streaming: bool = True) -> SimpleAgent:
    """Return a SimpleAgent whose ModelConfig has streaming=streaming."""
    config = Agent(
        name="lyra",
        system_prompt="You are Lyra.",
        memory_namespace="lyra",
        llm_config=ModelConfig(streaming=streaming),
    )
    return SimpleAgent(config, cast("LlmProvider", provider))


class TestSimpleAgentStreaming:
    """SimpleAgent.process() returns AsyncIterator when streaming=True."""

    async def test_returns_async_iterator_when_streaming_true(self) -> None:
        # Arrange — provider has a stream() method
        provider = MagicMock()
        fake_iterator = _fake_async_gen("Hello", " world")
        provider.stream = AsyncMock(return_value=fake_iterator)
        provider.is_alive = MagicMock(return_value=True)
        agent = make_streaming_agent(provider, streaming=True)
        msg = make_inbound_message("hello")
        pool = make_pool()

        # Act
        result = await agent.process(msg, pool)

        # Assert — S4: streaming path returns a StreamProcessor-wrapped AsyncIterator
        # (not the raw driver iterator, and not a Response).
        assert isinstance(result, AsyncIterator)
        assert not isinstance(result, Response)
        assert result is not fake_iterator

    async def test_returns_response_when_streaming_false(self) -> None:
        # Arrange — streaming=False falls through to complete()
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(result="done", session_id="s1")
        )
        provider.is_alive = MagicMock(return_value=True)
        agent = make_streaming_agent(provider, streaming=False)
        msg = make_inbound_message("hello")
        pool = make_pool()

        # Act
        result = await agent.process(msg, pool)

        # Assert — non-streaming path returns a Response
        assert isinstance(result, Response)
        assert result.content == "done"

    async def test_stream_called_with_correct_args(self) -> None:
        # Arrange
        provider = MagicMock()
        fake_iterator = _fake_async_gen("token")
        provider.stream = AsyncMock(return_value=fake_iterator)
        provider.is_alive = MagicMock(return_value=True)
        agent = make_streaming_agent(provider, streaming=True)
        msg = make_inbound_message("my question")
        pool = make_pool(pool_id="tg:main:user1")

        # Act
        await agent.process(msg, pool)

        # Assert — stream() called with the right pool_id, text, model_cfg,
        # system_prompt
        provider.stream.assert_awaited_once()
        args = provider.stream.call_args[0]
        assert args[0] == "tg:main:user1"
        assert args[1] == "<user_message>my question</user_message>"

    async def test_returns_response_when_provider_has_no_stream_method(self) -> None:
        # Arrange — provider without stream() falls back to complete()
        provider = MagicMock(spec=["complete", "is_alive"])
        provider.complete = AsyncMock(
            return_value=LlmResult(result="fallback", session_id="s1")
        )
        provider.is_alive = MagicMock(return_value=True)
        # Make sure hasattr(provider, 'stream') is False
        assert not hasattr(provider, "stream")

        agent = make_streaming_agent(provider, streaming=True)
        msg = make_inbound_message("hello")
        pool = make_pool()

        # Act
        result = await agent.process(msg, pool)

        # Assert — complete() used as fallback
        assert isinstance(result, Response)
        assert result.content == "fallback"

    async def test_streaming_uses_pool_system_prompt_when_set(self) -> None:
        # Arrange — pool has a custom system prompt override
        provider = MagicMock()
        fake_iterator = _fake_async_gen()
        provider.stream = AsyncMock(return_value=fake_iterator)
        provider.is_alive = MagicMock(return_value=True)
        agent = make_streaming_agent(provider, streaming=True)
        msg = make_inbound_message("hello")
        pool = make_pool()
        pool._system_prompt = "Custom system prompt"

        # Act
        await agent.process(msg, pool)

        # Assert — pool system prompt passed to stream()
        args = provider.stream.call_args[0]
        assert args[3] == "Custom system prompt"

    async def test_streaming_uses_agent_system_prompt_when_pool_prompt_absent(
        self,
    ) -> None:
        # Arrange — no pool system prompt → agent config system_prompt used
        provider = MagicMock()
        fake_iterator = _fake_async_gen()
        provider.stream = AsyncMock(return_value=fake_iterator)
        provider.is_alive = MagicMock(return_value=True)
        agent = make_streaming_agent(provider, streaming=True)
        msg = make_inbound_message("hello")
        pool = make_pool()
        # pool._system_prompt is None by default

        # Act
        await agent.process(msg, pool)

        # Assert — agent.config.system_prompt used
        args = provider.stream.call_args[0]
        assert args[3] == "You are Lyra."


# ---------------------------------------------------------------------------
# Helpers for CLI lifecycle tests
# ---------------------------------------------------------------------------


def make_agent_with_cli_pool(provider: object, cli_pool: object) -> SimpleAgent:
    """Return a SimpleAgent wired with an explicit cli_pool (T7)."""
    config = Agent(
        name="lyra",
        system_prompt="You are Lyra.",
        memory_namespace="lyra",
        llm_config=ModelConfig(),
    )
    return SimpleAgent(
        config, cast("LlmProvider", provider), cli_pool=cast("CliPool", cli_pool)
    )


# ---------------------------------------------------------------------------
# TestSimpleAgentCliLifecycle
# ---------------------------------------------------------------------------


class TestSimpleAgentCliLifecycle:
    """Regression tests for issue #620: all four CLI lifecycle ops route through
    self._cli_pool, not through getattr(self._provider, ...).

    Tests T8–T13 will be RED until the backend fix lands.
    """

    # ------------------------------------------------------------------
    # T8 — CB-wrapped reset: pool.reset_session() calls cli_pool.reset
    # ------------------------------------------------------------------

    async def test_t8_reset_routes_through_cli_pool(self) -> None:
        """T8: pool.reset_session() → cli_pool.reset(pool_id), not provider.reset."""
        # Arrange — provider has NO reset method
        provider = MagicMock(spec=["complete", "stream", "is_alive"])
        cli_pool = MagicMock()
        cli_pool.reset = AsyncMock()

        agent = make_agent_with_cli_pool(provider, cli_pool)
        pool = make_pool()
        agent.configure_pool(pool)

        # Act
        await pool.reset_session()

        # Assert — cli_pool.reset called with pool_id, not provider.reset
        cli_pool.reset.assert_called_once_with(pool.pool_id)

    # ------------------------------------------------------------------
    # T9a — CB-wrapped switch_cwd: workspace switch → cli_pool.switch_cwd
    # ------------------------------------------------------------------

    async def test_t9a_switch_cwd_routes_through_cli_pool(self) -> None:
        """T9a: _switch_workspace_fn routes to cli_pool.switch_cwd."""
        # Arrange — provider has NO switch_cwd method
        provider = MagicMock(spec=["complete", "stream", "is_alive"])
        cli_pool = MagicMock()
        cli_pool.switch_cwd = AsyncMock()

        agent = make_agent_with_cli_pool(provider, cli_pool)
        pool = make_pool()
        agent.configure_pool(pool)

        # Assert — _switch_workspace_fn was registered
        assert pool._switch_workspace_fn is not None

        # Act — invoke the registered callback directly
        await pool._switch_workspace_fn(Path("/new/cwd"))

        # Assert — cli_pool.switch_cwd called with pool_id and cwd
        cli_pool.switch_cwd.assert_called_once_with(pool.pool_id, Path("/new/cwd"))

    async def test_t9a_integration_switch_workspace_full_chain(self) -> None:
        """T9a-integration: pool.switch_workspace() routes to cli_pool.switch_cwd."""
        provider = MagicMock(spec=["complete", "stream", "is_alive"])
        cli_pool = MagicMock()
        cli_pool.switch_cwd = AsyncMock()

        agent = make_agent_with_cli_pool(provider, cli_pool)
        pool = make_pool()
        agent.configure_pool(pool)

        await pool.switch_workspace(Path("/new/cwd"))

        cli_pool.switch_cwd.assert_called_once_with(pool.pool_id, Path("/new/cwd"))

    # ------------------------------------------------------------------
    # T9b — CB-wrapped resume_and_reset: resume fn → cli_pool.resume_and_reset
    # ------------------------------------------------------------------

    async def test_t9b_resume_and_reset_routes_through_cli_pool(self) -> None:
        """T9b: _session_resume_fn routes to cli_pool.resume_and_reset."""
        # Arrange — provider has NO resume_and_reset method
        provider = MagicMock(spec=["complete", "stream", "is_alive"])
        cli_pool = MagicMock()
        cli_pool.resume_and_reset = AsyncMock(return_value=True)

        agent = make_agent_with_cli_pool(provider, cli_pool)
        pool = make_pool()
        agent.configure_pool(pool)

        # Assert — _session_resume_fn was registered (fix: route through cli_pool)
        assert pool._session_resume_fn is not None

        # Act
        await pool._session_resume_fn("sess-1")

        # Assert — cli_pool.resume_and_reset called with pool_id and session id
        cli_pool.resume_and_reset.assert_called_once_with(pool.pool_id, "sess-1")

    # ------------------------------------------------------------------
    # T10 — CB-wrapped link_lyra_session: process() → cli_pool.link_lyra_session
    # ------------------------------------------------------------------

    async def test_t10_link_lyra_session_routes_through_cli_pool(self) -> None:
        """T10: process() calls cli_pool.link_lyra_session(pool_id, session_id)."""
        # Arrange — provider has NO link_lyra_session but can complete
        provider = MagicMock(spec=["complete", "stream", "is_alive"])
        provider.complete = AsyncMock(
            return_value=LlmResult(result="hi", session_id="s1")
        )
        provider.is_alive = MagicMock(return_value=True)

        cli_pool = MagicMock()
        cli_pool.link_lyra_session = MagicMock()

        agent = make_agent_with_cli_pool(provider, cli_pool)
        pool = make_pool()
        agent.configure_pool(pool)

        # Act
        await agent.process(make_inbound_message("hi"), pool)

        # Assert — link_lyra_session called via cli_pool, not provider
        cli_pool.link_lyra_session.assert_called_once_with(
            pool.pool_id, pool.session_id
        )

    # ------------------------------------------------------------------
    # T11 — bare driver: all four ops still route through cli_pool
    # ------------------------------------------------------------------

    async def test_t11_bare_driver_all_four_ops_route_through_cli_pool(self) -> None:
        """T11: even when provider has all four methods, fix routes through cli_pool."""
        # Arrange — provider has ALL four methods
        provider = MagicMock()
        provider.reset = AsyncMock()
        provider.switch_cwd = AsyncMock()
        provider.resume_and_reset = AsyncMock(return_value=True)
        provider.link_lyra_session = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(result="ok", session_id="s1")
        )
        provider.is_alive = MagicMock(return_value=True)

        cli_pool = MagicMock()
        cli_pool.reset = AsyncMock()
        cli_pool.switch_cwd = AsyncMock()
        cli_pool.resume_and_reset = AsyncMock(return_value=True)
        cli_pool.link_lyra_session = MagicMock()

        agent = make_agent_with_cli_pool(provider, cli_pool)
        pool = make_pool()
        agent.configure_pool(pool)

        # Act — trigger each operation
        await pool.reset_session()
        if pool._switch_workspace_fn is not None:
            await pool._switch_workspace_fn(Path("/some/cwd"))
        if pool._session_resume_fn is not None:
            await pool._session_resume_fn("sess-42")
        await agent.process(make_inbound_message("hi"), pool)

        # Assert — all four went through cli_pool
        cli_pool.reset.assert_called_once_with(pool.pool_id)
        cli_pool.switch_cwd.assert_called_once_with(pool.pool_id, Path("/some/cwd"))
        cli_pool.resume_and_reset.assert_called_once_with(pool.pool_id, "sess-42")
        cli_pool.link_lyra_session.assert_called_once_with(
            pool.pool_id, pool.session_id
        )

    # ------------------------------------------------------------------
    # T12 — idempotency: configure_pool twice → callbacks registered once
    # ------------------------------------------------------------------

    async def test_t12_configure_pool_idempotent(self) -> None:
        """T12: calling configure_pool twice does not double-register callbacks."""
        # Arrange
        provider = MagicMock(spec=["complete", "stream", "is_alive"])
        cli_pool = MagicMock()
        cli_pool.reset = AsyncMock()

        agent = make_agent_with_cli_pool(provider, cli_pool)
        pool = make_pool()

        # Act — configure twice
        agent.configure_pool(pool)
        fn_after_first = pool._session_reset_fn
        agent.configure_pool(pool)
        fn_after_second = pool._session_reset_fn

        # Assert — same fn object registered (guard: if _session_reset_fn is None)
        assert fn_after_first is not None
        assert fn_after_second is fn_after_first

        # Calling reset_session must invoke cli_pool.reset exactly once
        await pool.reset_session()
        cli_pool.reset.assert_called_once_with(pool.pool_id)

    # ------------------------------------------------------------------
    # T13 — cli_pool=None: no errors, reset fn stays None
    # ------------------------------------------------------------------

    async def test_t13_no_cli_pool_no_errors(self) -> None:
        """T13: cli_pool=None → no AttributeError, _session_reset_fn stays None."""
        # Arrange — standard agent without cli_pool
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(result="ok", session_id="s1")
        )
        provider.is_alive = MagicMock(return_value=True)

        agent = make_agent(provider)
        pool = make_pool()

        # Act — configure_pool and process must not raise
        agent.configure_pool(pool)
        response = await agent.process(make_inbound_message("hi"), pool)

        # Assert — reset fn not registered, response returned cleanly
        assert pool._session_reset_fn is None
        assert isinstance(response, Response)


# ---------------------------------------------------------------------------
# TestSimpleAgentIsBackendAlive
# ---------------------------------------------------------------------------


class TestSimpleAgentIsBackendAlive:
    def test_delegates_to_provider(self) -> None:
        provider = MagicMock()
        provider.is_alive = MagicMock(return_value=True)
        agent = make_agent(provider)

        assert agent.is_backend_alive("pool-1") is True
        provider.is_alive.assert_called_once_with("pool-1")

    def test_returns_false_when_provider_says_false(self) -> None:
        provider = MagicMock()
        provider.is_alive = MagicMock(return_value=False)
        agent = make_agent(provider)

        assert agent.is_backend_alive("pool-1") is False


# ---------------------------------------------------------------------------
# TestSimpleAgentResetBackend
# ---------------------------------------------------------------------------


class TestSimpleAgentResetBackend:
    async def test_reset_routes_through_cli_pool(self) -> None:
        provider = MagicMock(spec=["complete", "stream", "is_alive"])
        cli_pool = MagicMock()
        cli_pool.reset = AsyncMock()
        agent = make_agent_with_cli_pool(provider, cli_pool)

        await agent.reset_backend("pool-1")

        cli_pool.reset.assert_awaited_once_with("pool-1")

    async def test_noop_when_no_cli_pool(self) -> None:
        provider = MagicMock()
        provider.reset = MagicMock()
        agent = make_agent(provider)

        await agent.reset_backend("pool-1")

        provider.reset.assert_not_called()


# ---------------------------------------------------------------------------
# TestSimpleAgentSessionToolsFailure
# ---------------------------------------------------------------------------


class TestSimpleAgentSessionToolsFailure:
    def test_session_tools_build_failure_sets_none(self, monkeypatch: Any) -> None:
        """When SessionTools construction fails, _session_tools is None and no crash."""
        from lyra.integrations import vault_cli, web_intel

        monkeypatch.setattr(
            web_intel, "WebIntelScraper", MagicMock(side_effect=RuntimeError("no bin"))
        )
        monkeypatch.setattr(vault_cli, "VaultCli", MagicMock())

        provider = MagicMock()
        agent = make_agent(provider)

        assert agent._session_tools is None


# ---------------------------------------------------------------------------
# TestSimpleAgentVoiceRewrite
# ---------------------------------------------------------------------------


class TestSimpleAgentVoiceRewrite:
    async def test_voice_command_rewrites_message(self) -> None:
        """'/voice prompt' rewrites msg with voice modality."""
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(result="spoken reply", session_id="s1")
        )
        tts = MagicMock()
        config = Agent(
            name="lyra",
            system_prompt="You are Lyra.",
            memory_namespace="lyra",
            llm_config=ModelConfig(),
        )
        agent = SimpleAgent(config, cast("LlmProvider", provider), tts=tts)
        msg = make_inbound_message("/voice tell me a joke")
        pool = make_pool()

        response = await agent.process(msg, pool)

        assert isinstance(response, Response)
        assert response.speak is True
        call_args = provider.complete.call_args[0]
        assert "tell me a joke" in call_args[1]
        assert "/voice tell" not in call_args[1]


# ---------------------------------------------------------------------------
# TestSimpleAgentEmptyReply
# ---------------------------------------------------------------------------


class TestSimpleAgentEmptyReply:
    async def test_empty_reply_returns_response_with_empty_content(self) -> None:
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(result="", session_id="s1")
        )
        agent = make_agent(provider)
        msg = make_inbound_message("hi")
        pool = make_pool()

        response = await agent.process(msg, pool)

        assert isinstance(response, Response)
        assert response.content == ""
        assert response.metadata["session_id"] == "s1"

    async def test_empty_reply_with_voice_modality_still_speaks(self) -> None:
        """Empty reply + voice modality → speak=True (intersection of both paths)."""
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(result="", session_id="s1")
        )
        tts = MagicMock()
        config = Agent(
            name="lyra",
            system_prompt="You are Lyra.",
            memory_namespace="lyra",
            llm_config=ModelConfig(),
        )
        agent = SimpleAgent(config, cast("LlmProvider", provider), tts=tts)
        msg = make_inbound_message("/voice say nothing")
        pool = make_pool()

        response = await agent.process(msg, pool)

        assert isinstance(response, Response)
        assert response.content == ""
        assert response.speak is True
