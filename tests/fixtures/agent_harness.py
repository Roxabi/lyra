"""Agent harness for integration tests.

Provides a fully-wired test environment with fake drivers for LLM, TTS, and STT.
"""

from __future__ import annotations

import asyncio
import tomllib
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

from tests.conftest import yield_once

from lyra.agents.simple_agent import SimpleAgent
from lyra.core.agent import Agent
from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import InboundMessage, Response
from lyra.core.pool import Pool

from .fake_drivers import FakeClaudeCliDriver, FakeStt, FakeTts

if TYPE_CHECKING:
    pass


MINIMAL_TOML = """
[agent]
name = "test"
system_prompt = "You are a test agent."

[model]
backend = "claude-cli"
model = "claude-sonnet-4-6"
"""


def _parse_toml_config(toml: str) -> Agent:
    """Parse TOML string into Agent config."""
    data = tomllib.loads(toml)
    agent_section = data.get("agent", {})
    model_section = data.get("model", {})

    name = agent_section.get("name") or model_section.get("name", "test")
    system_prompt = agent_section.get("system_prompt", "")
    memory_namespace = agent_section.get("memory_namespace", name)

    from lyra.core.agent.agent_config import ModelConfig

    model_cfg = ModelConfig(
        backend=model_section.get("backend", "claude-cli"),
        model=model_section.get("model", "claude-sonnet-4-6"),
    )

    return Agent(
        name=name,
        system_prompt=system_prompt,
        memory_namespace=memory_namespace,
        llm_config=model_cfg,
    )


def _make_ctx_mock(agents: dict | None = None) -> MagicMock:
    """Build a minimal PoolContext mock (mirrors tests/core/conftest.py)."""
    ctx = MagicMock()
    _agents: dict = agents or {}
    ctx.get_agent = MagicMock(side_effect=lambda name: _agents.get(name))
    ctx.get_message = MagicMock(return_value=None)
    ctx.dispatch_response = AsyncMock(return_value=None)
    ctx.dispatch_streaming = AsyncMock(return_value=None)
    ctx.record_circuit_success = MagicMock()
    ctx.record_circuit_failure = MagicMock()
    ctx._agents = _agents
    return ctx


async def _drain(pool: Pool, *, timeout: float = 2.0) -> None:
    """Yield to the event loop then wait for the current task to finish."""
    await yield_once()
    if pool._current_task is not None:
        await asyncio.wait_for(pool._current_task, timeout=timeout)


@dataclass
class AgentHarness:
    """Test harness for agent integration tests.

    Holds all components needed to test an agent with fake drivers.
    """

    agent: SimpleAgent
    driver: FakeClaudeCliDriver
    stt: FakeStt
    tts: FakeTts
    ctx: MagicMock  # PoolContext mock
    pool: Pool

    async def send(self, text: str, voice: str | None = None) -> Response:
        """Send a text message to the agent and return the response.

        Args:
            text: The message text to send.
            voice: Optional voice name to trigger TTS synthesis.

        Returns:
            The Response from the agent.
        """
        msg = InboundMessage(
            id="msg-1",
            platform="telegram",
            bot_id="main",
            scope_id="chat:1",
            user_id="tg:user:1",
            user_name="TestUser",
            is_mention=False,
            text=text,
            text_raw=text,
            trust_level=TrustLevel.TRUSTED,
            timestamp=datetime.now(timezone.utc),
            platform_meta={
                "chat_id": 1,
                "topic_id": None,
                "message_id": None,
                "is_group": False,
            },
        )

        if voice is not None:
            self.pool.voice_mode = True

        self.pool.submit(msg)
        await _drain(self.pool)

        # Extract response from dispatch_response mock
        self.ctx.dispatch_response.assert_awaited()
        # call_args[0][1] is the Response argument
        response: Response = self.ctx.dispatch_response.call_args[0][1]

        # Simulate TTS dispatch (which would happen in Hub's OutboundRouter)
        # In the real flow, voice_mode triggers TTS synthesis after response
        if voice is not None and response.content:
            await self.tts.synthesize(response.content, voice=voice)

        return response

    async def send_audio(self, audio_bytes: bytes, transcript: str) -> Response:
        """Simulate sending an audio message that was already transcribed by STT.

        In the real pipeline, STT middleware runs BEFORE the agent sees the message.
        This method simulates that by:
        1. Recording that STT was "called" with the audio bytes
        2. Sending a voice-modality message with the transcript pre-filled

        Args:
            audio_bytes: Raw audio data (recorded for assertion).
            transcript: The transcript (as if STT produced it).

        Returns:
            The Response from the agent.
        """
        # Record STT call for test assertions
        self.stt.called = True
        self.stt.last_audio = audio_bytes

        # Send as a voice-modality message with transcript already filled
        # (simulating post-STT-middleware state)
        msg = InboundMessage(
            id="msg-1",
            platform="telegram",
            bot_id="main",
            scope_id="chat:1",
            user_id="tg:user:1",
            user_name="TestUser",
            is_mention=False,
            text=transcript,
            text_raw=transcript,
            trust_level=TrustLevel.TRUSTED,
            timestamp=datetime.now(timezone.utc),
            modality="voice",
            platform_meta={
                "chat_id": 1,
                "topic_id": None,
                "message_id": None,
                "is_group": False,
            },
        )

        self.pool.submit(msg)
        await _drain(self.pool)

        # Extract response from dispatch_response mock
        self.ctx.dispatch_response.assert_awaited()
        response: Response = self.ctx.dispatch_response.call_args[0][1]
        return response

    def assert_tts_called_with(self, voice: str) -> None:
        """Assert that TTS was called with the specified voice.

        Args:
            voice: Expected voice name.

        Raises:
            AssertionError: If TTS was not called or voice doesn't match.
        """
        assert self.tts.called, "TTS was not called"
        assert self.tts.last_voice == voice, (
            f"TTS voice mismatch: expected {voice!r}, got {self.tts.last_voice!r}"
        )


@asynccontextmanager
async def agent_harness(
    agent_cls: type[SimpleAgent] = SimpleAgent,
    toml: str = MINIMAL_TOML,
) -> AsyncIterator[AgentHarness]:
    """Create a fully-wired agent test harness.

    Sets up:
    - SimpleAgent with FakeClaudeCliDriver
    - Pool with ctx_mock
    - FakeStt and FakeTts instances
    - Wires everything together

    Args:
        agent_cls: Agent class to instantiate (default: SimpleAgent).
        toml: TOML config string for the agent.

    Yields:
        AgentHarness with all components ready for testing.
    """
    driver = FakeClaudeCliDriver()
    stt = FakeStt()
    tts = FakeTts()

    config = _parse_toml_config(toml)

    agent = agent_cls(
        config=config,
        provider=driver,  # type: ignore[arg-type]
        stt=stt,
        tts=tts,
    )

    ctx = _make_ctx_mock(agents={config.name: agent})

    pool = Pool(
        pool_id=f"test:{config.name}:chat:1",
        agent_name=config.name,
        ctx=ctx,
        turn_timeout=60.0,
        debounce_ms=0,
    )

    # Wire the agent's pool reference if needed
    # (SimpleAgent doesn't need explicit pool wiring - it receives pool in process())

    harness = AgentHarness(
        agent=agent,
        driver=driver,
        stt=stt,
        tts=tts,
        ctx=ctx,
        pool=pool,
    )

    try:
        yield harness
    finally:
        # Cleanup: cancel any running task
        if pool._current_task is not None and not pool._current_task.done():
            pool._current_task.cancel()
            try:
                await pool._current_task
            except asyncio.CancelledError:
                pass
