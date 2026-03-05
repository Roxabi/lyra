"""Tests for the command router feature (issue #66).

RED phase — tests import from lyra.core.command_router which does not exist yet.
All tests are expected to FAIL until the backend-dev GREEN phase completes.

Covers:
  Slice 1 — Command detection + registry (tests 1–7)
  Slice 2 — Skill execution + args (tests 8–11)
  Slice 3 — Error handling (tests 12–14)
"""

from __future__ import annotations

import asyncio
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from lyra.core.agent import Agent, AgentBase, load_agent_config
from lyra.core.command_router import CommandConfig, CommandRouter, SkillHandler
from lyra.core.hub import Hub
from lyra.core.message import Message, MessageType, Platform, Response, TelegramContext
from lyra.core.pool import Pool

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def make_message(
    content: str = "hello",
    platform: Platform = Platform.TELEGRAM,
    bot_id: str = "main",
    user_id: str = "alice",
) -> Message:
    """Build a minimal Message for testing. Uses direct construction (not
    from_adapter) so that content can be a plain string as the router receives."""
    return Message(
        id="msg-test-1",
        platform=platform,
        bot_id=bot_id,
        user_id=user_id,
        user_name="Alice",
        is_mention=False,
        is_from_bot=False,
        content=content,
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
        platform_context=TelegramContext(chat_id=42),
    )


def make_router(
    extra_commands: dict[str, CommandConfig] | None = None,
) -> CommandRouter:
    """Build a CommandRouter with /help and /echo registered by default."""
    commands: dict[str, CommandConfig] = {
        "/help": CommandConfig(builtin=True, description="List available commands"),
        "/echo": CommandConfig(
            skill="echo",
            action="echo",
            cli="echo",
            description="Echo back the message (test command)",
        ),
    }
    if extra_commands:
        commands.update(extra_commands)
    return CommandRouter(commands=commands)


def make_minimal_toml(extra: str = "") -> str:
    """Return a minimal valid agent TOML string."""
    return textwrap.dedent(
        f"""
        [agent]
        name = "test_agent"
        memory_namespace = "test"
        permissions = []

        [model]
        backend = "claude-cli"
        model = "claude-haiku-4-5-20251001"
        max_turns = 10
        tools = []

        [prompt]
        system = "You are a test agent."

        {extra}
        """
    ).strip()


# ---------------------------------------------------------------------------
# Slice 1 — Command Detection + Registry
# ---------------------------------------------------------------------------


class TestIsCommand:
    """is_command() detects slash-prefixed messages (SC-1)."""

    def test_is_command_detects_slash_prefix(self) -> None:
        # Arrange
        router = make_router()
        msg = make_message(content="/help")

        # Act
        result = router.is_command(msg)

        # Assert
        assert result is True

    def test_is_command_ignores_plain_text(self) -> None:
        # Arrange
        router = make_router()
        msg = make_message(content="hello")

        # Act
        result = router.is_command(msg)

        # Assert
        assert result is False

    def test_is_command_ignores_slash_in_middle(self) -> None:
        # Arrange
        router = make_router()
        msg = make_message(content="not a /command")

        # Act
        result = router.is_command(msg)

        # Assert
        assert result is False

    def test_is_command_empty_string_is_not_command(self) -> None:
        # Arrange
        router = make_router()
        msg = make_message(content="")

        # Act
        result = router.is_command(msg)

        # Assert
        assert result is False

    def test_is_command_bare_slash_is_not_command(self) -> None:
        # Arrange — a lone "/" with no name is ambiguous; spec requires a name after "/"
        router = make_router()
        msg = make_message(content="/")

        # Act
        result = router.is_command(msg)

        # Assert — "/" alone should NOT be treated as a command (no command name follows)  # noqa: E501
        assert result is False


class TestDispatchHelp:
    """dispatch() for /help returns a listing of all registered commands (SC-4)."""

    @pytest.mark.asyncio
    async def test_dispatch_help_lists_commands(self) -> None:
        # Arrange
        router = make_router()
        msg = make_message(content="/help")

        # Act
        response = await router.dispatch(msg)

        # Assert — response text must mention both registered commands
        assert isinstance(response, Response)
        assert "/help" in response.content
        assert "/echo" in response.content

    @pytest.mark.asyncio
    async def test_dispatch_help_includes_descriptions(self) -> None:
        # Arrange
        router = make_router()
        msg = make_message(content="/help")

        # Act
        response = await router.dispatch(msg)

        # Assert — descriptions appear in the listing
        assert "List available commands" in response.content
        assert "Echo back the message" in response.content


class TestDispatchUnknownCommand:
    """dispatch() for an unrecognised command returns a helpful error (SC-5)."""

    @pytest.mark.asyncio
    async def test_dispatch_unknown_command(self) -> None:
        # Arrange
        router = make_router()
        msg = make_message(content="/pizza")

        # Act
        response = await router.dispatch(msg)

        # Assert — "Unknown command" and /help hint in the reply
        assert isinstance(response, Response)
        assert "unknown command" in response.content.lower()
        assert "/help" in response.content


class TestCommandConfigFromToml:
    """load_agent_config() parses the [commands] TOML section (SC-2)."""

    def test_command_config_from_toml(self) -> None:
        # Arrange — write a temporary TOML file that includes [commands]
        commands_toml = textwrap.dedent(
            """
            [commands."/help"]
            builtin = true
            description = "List available commands"

            [commands."/echo"]
            skill = "echo"
            action = "echo"
            cli = "echo"
            description = "Echo back the message (test command)"
            """
        )
        toml_content = make_minimal_toml(extra=commands_toml)

        with tempfile.TemporaryDirectory() as tmpdir:
            toml_path = Path(tmpdir) / "test_agent.toml"
            toml_path.write_text(toml_content)

            # Act
            config = load_agent_config("test_agent", agents_dir=Path(tmpdir))

        # Assert — Agent.commands dict contains both commands
        assert hasattr(config, "commands"), "Agent.commands attribute missing"
        assert "/help" in config.commands
        assert "/echo" in config.commands

        echo_cfg = config.commands["/echo"]
        assert isinstance(echo_cfg, CommandConfig)
        assert echo_cfg.skill == "echo"
        assert echo_cfg.action == "echo"
        assert echo_cfg.cli == "echo"
        assert echo_cfg.description == "Echo back the message (test command)"

        help_cfg = config.commands["/help"]
        assert help_cfg.builtin is True


class TestHotReloadUpdatesCommands:
    """_maybe_reload() updates the command_router when TOML changes (SC-3)."""

    def test_hot_reload_updates_commands(self) -> None:
        # Arrange — create a concrete AgentBase subclass for testing
        class ConcreteAgent(AgentBase):
            async def process(self, msg: Message, pool: Pool) -> Response:
                return Response(content="ok")

        initial_toml = make_minimal_toml(
            extra=textwrap.dedent(
                """
                [commands."/help"]
                builtin = true
                description = "List available commands"
                """
            )
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            toml_path = Path(tmpdir) / "test_agent.toml"
            toml_path.write_text(initial_toml)

            config = load_agent_config("test_agent", agents_dir=Path(tmpdir))
            agent = ConcreteAgent(config, agents_dir=Path(tmpdir))

            # Verify initial state — only /help is registered
            assert "/help" in agent.command_router.commands
            assert "/pizza" not in agent.command_router.commands

            # Act — update the TOML to add a new command
            updated_toml = make_minimal_toml(
                extra=textwrap.dedent(
                    """
                    [commands."/help"]
                    builtin = true
                    description = "List available commands"

                    [commands."/pizza"]
                    skill = "food"
                    action = "order"
                    cli = "pizza-cli"
                    description = "Order a pizza"
                    """
                )
            )
            toml_path.write_text(updated_toml)

            # Force mtime to be strictly newer (filesystem mtime granularity)

            new_mtime = agent._last_mtime + 1
            import os

            os.utime(toml_path, (new_mtime, new_mtime))

            agent._maybe_reload()

        # Assert — command_router now includes the new command
        assert "/pizza" in agent.command_router.commands


# ---------------------------------------------------------------------------
# Slice 2 — Skill Execution
# ---------------------------------------------------------------------------


class TestSkillHandlerExecute:
    """SkillHandler.execute() runs the CLI and returns stdout (SC-7, SC-8, SC-9)."""

    @pytest.mark.asyncio
    async def test_execute_echo_returns_stdout(self) -> None:
        # Arrange — "echo" binary is always available on the test system
        # Act
        result = await SkillHandler.execute(
            skill="echo", action="echo", args=["hello"]
        )

        # Assert
        assert result.strip() == "hello"

    @pytest.mark.asyncio
    async def test_execute_with_multiple_args(self) -> None:
        # Arrange
        # Act
        result = await SkillHandler.execute(
            skill="echo", action="echo", args=["foo", "bar", "baz"]
        )

        # Assert
        assert result.strip() == "foo bar baz"

    @pytest.mark.asyncio
    async def test_execute_timeout(self) -> None:
        """A slow subprocess that exceeds the timeout returns a 'timed out' message
        instead of raising. The mock replaces asyncio.wait_for to simulate a timeout."""
        # Arrange — mock wait_for to raise TimeoutError immediately
        with patch(
            "lyra.core.command_router.asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ):
            # Act
            result = await SkillHandler.execute(
                skill="echo",
                action="echo",
                args=["hello"],
                timeout=0.001,
            )

        # Assert — caller receives a user-facing timeout message (not an exception)
        assert "timed out" in result.lower()


class TestDispatchRoutesToSkill:
    """dispatch() routes a skill command through SkillHandler (SC-7)."""

    @pytest.mark.asyncio
    async def test_dispatch_routes_to_skill(self) -> None:
        # Arrange
        router = make_router()
        msg = make_message(content="/echo hi")

        # Act — patch SkillHandler.execute to capture the call
        with patch.object(
            SkillHandler,
            "execute",
            new_callable=AsyncMock,
            return_value="hi",
        ) as mock_execute:
            response = await router.dispatch(msg)

        # Assert — SkillHandler.execute was called and result surfaced in Response
        mock_execute.assert_awaited_once()
        assert isinstance(response, Response)
        assert "hi" in response.content


# ---------------------------------------------------------------------------
# Slice 3 — Error Handling
# ---------------------------------------------------------------------------


class TestCliNotFound:
    """CLI binary absent from PATH → user-friendly 'not installed' message (SC-6)."""

    @pytest.mark.asyncio
    async def test_cli_not_found(self) -> None:
        # Arrange — register a command whose cli binary cannot be found
        router = make_router(
            extra_commands={
                "/ghost": CommandConfig(
                    skill="ghost",
                    action="run",
                    cli="nonexistent_binary_xyz",
                    description="A command whose binary is missing",
                )
            }
        )
        msg = make_message(content="/ghost")

        # Act — shutil.which("nonexistent_binary_xyz") returns None on any system
        response = await router.dispatch(msg)

        # Assert
        assert isinstance(response, Response)
        assert "not installed" in response.content.lower()

    @pytest.mark.asyncio
    async def test_cli_not_found_mentions_binary_name(self) -> None:
        # Arrange
        router = make_router(
            extra_commands={
                "/ghost": CommandConfig(
                    skill="ghost",
                    action="run",
                    cli="nonexistent_binary_xyz",
                    description="Missing CLI test",
                )
            }
        )
        msg = make_message(content="/ghost")

        # Act
        response = await router.dispatch(msg)

        # Assert — the response names the missing binary so the user knows what to install  # noqa: E501
        assert "nonexistent_binary_xyz" in response.content


class TestTimeoutProducesUserMessage:
    """A subprocess timeout results in a polite 'timed out' user message (SC-8)."""

    @pytest.mark.asyncio
    async def test_timeout_produces_user_message(self) -> None:
        # Arrange
        router = make_router()
        msg = make_message(content="/echo slow")

        # Patch SkillHandler.execute to simulate a timeout at the dispatch level
        with patch.object(
            SkillHandler,
            "execute",
            new_callable=AsyncMock,
            return_value="Command timed out. Please try again.",
        ):
            response = await router.dispatch(msg)

        # Assert
        assert isinstance(response, Response)
        assert "timed out" in response.content.lower()


class TestPassthroughNonCommandInHub:
    """Plain-text messages bypass the command router and reach agent.process() (SC-10)."""  # noqa: E501

    @pytest.mark.asyncio
    async def test_passthrough_non_command_in_hub(self) -> None:
        # Arrange — wire up a full hub with a capturing agent
        process_calls: list[Message] = []

        class CapturingAgent(AgentBase):
            async def process(self, msg: Message, pool: Pool) -> Response:
                process_calls.append(msg)
                return Response(content="agent reply")

        hub = Hub()

        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")
        agent = CapturingAgent(config)
        hub.register_agent(agent)

        class CapturingAdapter:
            async def send(self, original_msg: Message, response: Response) -> None:
                pass

        hub.register_adapter(Platform.TELEGRAM, "main", CapturingAdapter())
        hub.register_binding(
            Platform.TELEGRAM, "main", "alice", "lyra", "telegram:main:alice"
        )

        plain_msg = make_message(content="hello, how are you?")
        await hub.bus.put(plain_msg)

        # Act — run the hub for one iteration
        try:
            await asyncio.wait_for(hub.run(), timeout=0.3)
        except asyncio.TimeoutError:
            pass  # expected — hub.run() never returns on its own

        # Assert — agent.process() was called with the plain-text message
        assert len(process_calls) == 1
        assert process_calls[0].content == "hello, how are you?"

    @pytest.mark.asyncio
    async def test_slash_command_does_not_reach_agent_process(self) -> None:
        """When the command router handles a /command, agent.process() is never called."""  # noqa: E501
        # Arrange
        process_calls: list[Message] = []

        class TrackingAgent(AgentBase):
            async def process(self, msg: Message, pool: Pool) -> Response:
                process_calls.append(msg)
                return Response(content="should not be reached")

        hub = Hub()
        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")
        agent = TrackingAgent(config)
        hub.register_agent(agent)

        class CapturingAdapter:
            async def send(self, original_msg: Message, response: Response) -> None:
                pass

        hub.register_adapter(Platform.TELEGRAM, "main", CapturingAdapter())
        hub.register_binding(
            Platform.TELEGRAM, "main", "alice", "lyra", "telegram:main:alice"
        )

        command_msg = make_message(content="/help")
        await hub.bus.put(command_msg)

        # Act
        try:
            await asyncio.wait_for(hub.run(), timeout=0.3)
        except asyncio.TimeoutError:
            pass

        # Assert — command router intercepted the message; agent.process() was skipped
        assert len(process_calls) == 0
