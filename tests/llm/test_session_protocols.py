"""Tests for SessionAware and WorkspaceAware capability protocols (llm.py)."""

from pathlib import Path

from factory.core.ports.llm import SessionAware, WorkspaceAware

# ---------------------------------------------------------------------------
# SessionAware
# ---------------------------------------------------------------------------


class _FullSessionAware:
    """Dummy that satisfies all three SessionAware methods."""

    def link_lyra_session(self, pool_id: str, lyra_session_id: str) -> None:
        pass

    async def reset(self, pool_id: str) -> None:
        pass

    async def queue_resume(self, pool_id: str, session_id: str) -> bool:
        return True


class _MissingQueueResume:
    """Dummy missing ``queue_resume`` — must NOT satisfy SessionAware."""

    def link_lyra_session(self, pool_id: str, lyra_session_id: str) -> None:
        pass

    async def reset(self, pool_id: str) -> None:
        pass


def test_session_aware_is_runtime_checkable() -> None:
    assert isinstance(_FullSessionAware(), SessionAware)


def test_session_aware_missing_method_fails() -> None:
    assert not isinstance(_MissingQueueResume(), SessionAware)


# ---------------------------------------------------------------------------
# WorkspaceAware
# ---------------------------------------------------------------------------


class _FullWorkspaceAware:
    """Dummy that satisfies the single WorkspaceAware method."""

    async def switch_cwd(self, pool_id: str, cwd: Path) -> None:
        pass


class _MissingSwitchCwd:
    """Dummy missing ``switch_cwd`` — must NOT satisfy WorkspaceAware."""

    pass


def test_workspace_aware_is_runtime_checkable() -> None:
    assert isinstance(_FullWorkspaceAware(), WorkspaceAware)


def test_workspace_aware_missing_method_fails() -> None:
    assert not isinstance(_MissingSwitchCwd(), WorkspaceAware)
