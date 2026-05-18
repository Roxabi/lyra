"""Tests for _spawn() layered identity env-merge gate (#1150).

The gate has three modes:
- Full mode: agent_name + agent_email both present → injects all 4 vars
  (GIT_COMMITTER_NAME, GIT_COMMITTER_EMAIL, LYRA_AGENT, LYRA_SESSION_ID).
- Trailers-only mode: agent_name present, agent_email=None → injects
  LYRA_AGENT + LYRA_SESSION_ID only (no GIT_COMMITTER_* vars).
- Off: agent_name=None → no identity vars injected (image-baked identity).

_SAFE_ENV_KEYS must NOT contain any GIT_* or LYRA_* key — those are
synthesised at spawn time, not inherited from the parent environment.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from lyra.core.cli.cli_pool import CliPool
from lyra.core.cli.cli_pool_worker import _SAFE_ENV_KEYS

from .conftest_cli_pool import (
    _PATCH_TARGET,
    ASSISTANT_LINE,
    DEFAULT_MODEL,
    INIT_LINE,
    RESULT_LINE,
    make_fake_proc,
)


class TestSpawnLayeredIdentityGate:
    """_spawn() injects identity env vars based on which fields are present."""

    async def test_full_mode_injects_all_four_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Full mode: agent_name + agent_email + lyra_session_id → all 4 vars injected.

        GIT_COMMITTER_NAME, GIT_COMMITTER_EMAIL, LYRA_AGENT, LYRA_SESSION_ID
        must all be present in the subprocess env with matching values.
        """
        # Arrange
        proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, RESULT_LINE])
        spawn_mock = AsyncMock(return_value=proc)
        pool = CliPool()

        # Act
        with patch(_PATCH_TARGET, new=spawn_mock):
            await pool.send(
                "pool-1",
                "hello",
                DEFAULT_MODEL,
                agent_name="agent-X",
                agent_email="x@y.com",
                lyra_session_id="S-123",
            )

        # Assert
        env = spawn_mock.call_args.kwargs["env"]
        assert env.get("GIT_COMMITTER_NAME") == "agent-X"
        assert env.get("GIT_COMMITTER_EMAIL") == "x@y.com"
        assert env.get("LYRA_AGENT") == "agent-X"
        assert env.get("LYRA_SESSION_ID") == "S-123"

    async def test_trailers_only_mode_omits_git_committer_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Trailers-only mode: agent_name present, agent_email=None.

        LYRA_AGENT and LYRA_SESSION_ID are injected; GIT_COMMITTER_NAME and
        GIT_COMMITTER_EMAIL must be absent (subprocess falls back to the
        image-baked template identity for the committer field).
        """
        # Arrange
        proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, RESULT_LINE])
        spawn_mock = AsyncMock(return_value=proc)
        pool = CliPool()

        # Act
        with patch(_PATCH_TARGET, new=spawn_mock):
            await pool.send(
                "pool-1",
                "hello",
                DEFAULT_MODEL,
                agent_name="agent-X",
                agent_email=None,
                lyra_session_id="S-456",
            )

        # Assert
        env = spawn_mock.call_args.kwargs["env"]
        assert env.get("LYRA_AGENT") == "agent-X"
        assert env.get("LYRA_SESSION_ID") == "S-456"
        assert "GIT_COMMITTER_NAME" not in env
        assert "GIT_COMMITTER_EMAIL" not in env

    async def test_off_mode_injects_no_identity_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Off mode: agent_name=None → none of the 4 identity vars injected.

        The subprocess spawns successfully and falls back entirely to the
        image-baked template identity (legacy-hub forward-compat path).
        """
        # Arrange
        proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, RESULT_LINE])
        spawn_mock = AsyncMock(return_value=proc)
        pool = CliPool()

        # Act
        with patch(_PATCH_TARGET, new=spawn_mock):
            result = await pool.send(
                "pool-1",
                "hello",
                DEFAULT_MODEL,
                agent_name=None,
                agent_email=None,
                lyra_session_id="S-789",
            )

        # Assert — spawn succeeded
        assert result.ok

        env = spawn_mock.call_args.kwargs["env"]
        assert "GIT_COMMITTER_NAME" not in env
        assert "GIT_COMMITTER_EMAIL" not in env
        assert "LYRA_AGENT" not in env
        assert "LYRA_SESSION_ID" not in env

    def test_safe_env_keys_contains_no_git_or_lyra_keys(self) -> None:
        """_SAFE_ENV_KEYS must not contain any GIT_* or LYRA_* key (#1150 SC8).

        Identity vars are synthesised at spawn time via an explicit post-filter
        merge step, not by extending the inherited-env allowlist.  This test
        is a merge-blocker: deleting the guard from _spawn would still leave
        _SAFE_ENV_KEYS clean, but adding a GIT_* or LYRA_* key to _SAFE_ENV_KEYS
        would violate the design contract.
        """
        # Arrange / Act
        git_or_lyra_in_allowlist = {
            k for k in _SAFE_ENV_KEYS if k.startswith(("GIT_", "LYRA_"))
        }

        # Assert
        assert not git_or_lyra_in_allowlist, (
            f"_SAFE_ENV_KEYS must not contain GIT_* or LYRA_* keys "
            f"(found: {git_or_lyra_in_allowlist})"
        )
