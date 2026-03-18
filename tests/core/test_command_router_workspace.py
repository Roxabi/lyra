"""Tests for workspace slash commands (TestWorkspaceCommands)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lyra.core.command_loader import CommandLoader
from lyra.core.command_router import CommandRouter
from lyra.core.message import Response
from lyra.core.pool import Pool

from .conftest import make_message

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_workspace_router(tmp_path: Path, workspaces: dict[str, Path]) -> CommandRouter:
    """Build a CommandRouter with workspace commands registered."""
    plugins_dir = Path(tempfile.mkdtemp())
    loader = CommandLoader(plugins_dir)
    return CommandRouter(
        command_loader=loader,
        enabled_plugins=[],
        workspaces=workspaces,
    )


# ---------------------------------------------------------------------------
# Workspace commands
# ---------------------------------------------------------------------------


class TestWorkspaceCommands:
    """Workspace slash commands switch cwd, clear history, re-submit args."""

    def test_workspace_builtin_registered(self, tmp_path: Path) -> None:
        """/workspace is registered as a builtin."""
        ws_dir = tmp_path / "myws"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"myws": ws_dir})
        assert "/workspace" in router._builtins

    def test_individual_workspace_names_not_registered_as_builtins(
        self, tmp_path: Path
    ) -> None:
        """Workspace names are NOT individually registered as builtins."""
        ws_dir = tmp_path / "myws"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"myws": ws_dir})
        assert "/myws" not in router._builtins

    @pytest.mark.asyncio
    async def test_workspace_ls_returns_list(self, tmp_path: Path) -> None:
        """/workspace ls returns the list of configured workspaces."""
        ws_dir = tmp_path / "proj"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"proj": ws_dir})
        msg = make_message(content="/workspace ls", is_admin=True)

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "proj" in response.content

    @pytest.mark.asyncio
    async def test_workspace_list_alias(self, tmp_path: Path) -> None:
        """/workspace list is an alias for /workspace ls."""
        ws_dir = tmp_path / "proj"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"proj": ws_dir})
        msg = make_message(content="/workspace list", is_admin=True)

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "proj" in response.content

    @pytest.mark.asyncio
    async def test_workspace_no_args_returns_list(self, tmp_path: Path) -> None:
        """/workspace with no args returns the list of workspaces."""
        ws_dir = tmp_path / "proj"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"proj": ws_dir})
        msg = make_message(content="/workspace", is_admin=True)

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "proj" in response.content

    @pytest.mark.asyncio
    async def test_workspace_dispatch_returns_context_response(
        self, tmp_path: Path
    ) -> None:
        """Dispatching /workspace <name> switches cwd and returns confirmation."""
        ws_dir = tmp_path / "proj"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"proj": ws_dir})
        msg = make_message(content="/workspace proj", is_admin=True)

        switch_called_with: list[Path] = []

        async def _fake_switch(cwd: Path) -> None:
            switch_called_with.append(cwd)

        pool_mock = MagicMock(spec=Pool)
        pool_mock.switch_workspace = _fake_switch
        pool_mock.submit = MagicMock()

        response = await router.dispatch(msg, pool=pool_mock)

        assert isinstance(response, Response)
        assert "Workspace: proj" in response.content
        assert switch_called_with == [ws_dir]

    @pytest.mark.asyncio
    async def test_workspace_dispatch_resubmits_remaining_args(
        self, tmp_path: Path
    ) -> None:
        """Remaining text after /workspace <name> is re-submitted via pool.submit."""
        ws_dir = tmp_path / "proj"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"proj": ws_dir})
        msg = make_message(
            content="/workspace proj what's the last commit?", is_admin=True
        )

        async def _fake_switch(cwd: Path) -> None:
            pass

        submitted: list = []
        pool_mock = MagicMock(spec=Pool)
        pool_mock.switch_workspace = _fake_switch
        pool_mock.submit = MagicMock(side_effect=submitted.append)

        await router.dispatch(msg, pool=pool_mock)

        pool_mock.submit.assert_called_once()
        submitted_msg = submitted[0]
        assert submitted_msg.text == "what's the last commit?"

    @pytest.mark.asyncio
    async def test_workspace_dispatch_no_pool_still_returns_context(
        self, tmp_path: Path
    ) -> None:
        """Without a pool, /workspace <name> still returns confirmation."""
        ws_dir = tmp_path / "solo"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"solo": ws_dir})
        msg = make_message(content="/workspace solo", is_admin=True)

        response = await router.dispatch(msg, pool=None)

        assert isinstance(response, Response)
        assert "Workspace: solo" in response.content

    @pytest.mark.asyncio
    async def test_unknown_workspace_name_returns_error(self, tmp_path: Path) -> None:
        """/workspace <unknown> returns an error listing available names."""
        ws_dir = tmp_path / "myws"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"myws": ws_dir})
        msg = make_message(content="/workspace otherws", is_admin=True)

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert response.content is not None
        assert "unknown workspace" in response.content.lower()
        assert "myws" in response.content
