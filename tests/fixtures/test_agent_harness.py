"""Tests for agent_harness integration test utilities."""

import pytest

from lyra.core.messaging.message import Response

from .agent_harness import agent_harness


class TestAgentHarness:
    """Tests for AgentHarness context manager."""

    @pytest.mark.asyncio
    async def test_context_manager_returns_harness(self) -> None:
        """agent_harness returns an AgentHarness instance."""
        async with agent_harness() as h:
            assert h.agent is not None
            assert h.driver is not None
            assert h.stt is not None
            assert h.tts is not None
            assert h.pool is not None
            assert h.ctx is not None

    @pytest.mark.asyncio
    async def test_send_returns_response(self) -> None:
        """h.send(text) returns a Response with queued content."""
        async with agent_harness() as h:
            h.driver.queue_response("Hello back!", session_id="test-sess")
            resp = await h.send("hello")
            assert isinstance(resp, Response)
            assert resp.content == "Hello back!"

    @pytest.mark.asyncio
    async def test_send_audio_triggers_stt(self) -> None:
        """h.send_audio sets preset_transcript and triggers STT path."""
        async with agent_harness() as h:
            h.driver.queue_response("I heard you say 'hi'", session_id="test-sess")
            resp = await h.send_audio(b"fake audio bytes", transcript="hi")
            assert h.stt.called
            assert isinstance(resp, Response)

    @pytest.mark.asyncio
    async def test_send_with_voice_triggers_tts(self) -> None:
        """h.send with voice= triggers TTS synthesis."""
        async with agent_harness() as h:
            h.driver.queue_response("Speaking now", session_id="test-sess")
            await h.send("speak this", voice="Sohee")
            h.assert_tts_called_with(voice="Sohee")

    @pytest.mark.asyncio
    async def test_assert_tts_called_with_fails_when_not_called(self) -> None:
        """assert_tts_called_with raises when TTS was not called."""
        async with agent_harness() as h:
            h.driver.queue_response("No speech", session_id="test-sess")
            await h.send("no voice here")
            with pytest.raises(AssertionError, match="TTS was not called"):
                h.assert_tts_called_with(voice="Sohee")

    @pytest.mark.asyncio
    async def test_send_returns_error_on_driver_error(self) -> None:
        """h.send returns error Response when driver queues error."""
        async with agent_harness() as h:
            h.driver.queue_error("Something went wrong")
            resp = await h.send("hello")
            assert isinstance(resp, Response)
            # Error content should be present

    @pytest.mark.asyncio
    async def test_send_audio_handles_gracefully(self) -> None:
        """h.send_audio returns Response even with invalid input."""
        async with agent_harness() as h:
            h.driver.queue_response("I heard you", session_id="test-sess")
            resp = await h.send_audio(b"fake audio", transcript="hello")
            assert isinstance(resp, Response)
            assert h.stt.called  # STT flag was set

    @pytest.mark.asyncio
    async def test_custom_toml_config(self) -> None:
        """agent_harness accepts custom TOML config."""
        custom_toml = """
[agent]
name = "custom_agent"
system_prompt = "You are a custom test agent."

[model]
backend = "claude-cli"
model = "claude-sonnet-4-6"
"""
        async with agent_harness(toml=custom_toml) as h:
            assert h.agent.config.name == "custom_agent"
            assert "custom test agent" in h.agent.config.system_prompt

    @pytest.mark.asyncio
    async def test_multi_turn_conversation(self) -> None:
        """Multiple sends in sequence work correctly."""
        async with agent_harness() as h:
            # First turn
            h.driver.queue_response("Hello!", session_id="sess-1")
            resp1 = await h.send("hi")
            assert resp1.content == "Hello!"

            # Second turn
            h.driver.queue_response("How can I help?", session_id="sess-2")
            resp2 = await h.send("what can you do?")
            assert resp2.content == "How can I help?"
