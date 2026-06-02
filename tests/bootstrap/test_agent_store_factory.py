"""Tests for bootstrap factory agent_store_factory — make_agent_store env routing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from factory.bootstrap.factory import agent_store_factory as factory_mod


def _patch_classes(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    """Patch JsonAgentStore and AgentStore at their source modules.

    The factory uses lazy imports inside ``make_agent_store``, so we patch the
    original classes so the local import resolves to the mock.
    """
    mock_json_cls = MagicMock()
    mock_sqlite_cls = MagicMock()
    monkeypatch.setattr(
        "factory.core.stores.json_agent_store.JsonAgentStore", mock_json_cls
    )
    monkeypatch.setattr(
        "factory.infrastructure.stores.agent_store.AgentStore", mock_sqlite_cls
    )
    return mock_json_cls, mock_sqlite_cls


# ---------------------------------------------------------------------------
# FACTORY_DB unset → AgentStore with default path
# ---------------------------------------------------------------------------


def test_make_agent_store_default_lyra_db_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FACTORY_DB unset → AgentStore with default ~/.roxabi/factory/config.db."""
    monkeypatch.delenv("FACTORY_DB", raising=False)
    monkeypatch.delenv("ROXABI_FACTORY_DIR", raising=False)
    mock_json, mock_sqlite = _patch_classes(monkeypatch)

    result = factory_mod.make_agent_store()

    mock_json.assert_not_called()
    mock_sqlite.assert_called_once()
    assert result is mock_sqlite.return_value
    _call_kwargs = mock_sqlite.call_args.kwargs
    expected_default = Path.home() / ".roxabi" / "factory" / "config.db"
    assert _call_kwargs["db_path"] == expected_default


# ---------------------------------------------------------------------------
# FACTORY_DB=json → JsonAgentStore with default or env path
# ---------------------------------------------------------------------------


def test_make_agent_store_json_mode_default_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FACTORY_DB=json with no FACTORY_AGENT_STORE_PATH → default path."""
    monkeypatch.setenv("FACTORY_DB", "json")
    monkeypatch.delenv("FACTORY_AGENT_STORE_PATH", raising=False)
    monkeypatch.delenv("ROXABI_FACTORY_DIR", raising=False)
    mock_json, mock_sqlite = _patch_classes(monkeypatch)

    result = factory_mod.make_agent_store()

    mock_sqlite.assert_not_called()
    mock_json.assert_called_once()
    assert result is mock_json.return_value
    _call_kwargs = mock_json.call_args.kwargs
    expected_default = Path.home() / ".roxabi" / "factory" / "agents_test.json"
    assert _call_kwargs["path"] == expected_default


def test_make_agent_store_json_mode_with_agent_store_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FACTORY_DB=json + FACTORY_AGENT_STORE_PATH → JsonAgentStore at that path."""
    monkeypatch.setenv("FACTORY_DB", "json")
    monkeypatch.setenv("FACTORY_AGENT_STORE_PATH", "/tmp/custom_agents.json")
    mock_json, mock_sqlite = _patch_classes(monkeypatch)

    result = factory_mod.make_agent_store()

    mock_sqlite.assert_not_called()
    mock_json.assert_called_once()
    assert result is mock_json.return_value
    assert mock_json.call_args.kwargs["path"] == Path("/tmp/custom_agents.json")


# ---------------------------------------------------------------------------
# db_path argument overrides default when FACTORY_DB unset
# ---------------------------------------------------------------------------


def test_make_agent_store_db_path_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """db_path argument overrides the default when FACTORY_DB is not set."""
    monkeypatch.delenv("FACTORY_DB", raising=False)
    mock_json, mock_sqlite = _patch_classes(monkeypatch)
    custom = Path("/tmp/override.db")

    result = factory_mod.make_agent_store(db_path=custom)

    mock_json.assert_not_called()
    mock_sqlite.assert_called_once()
    assert result is mock_sqlite.return_value
    assert mock_sqlite.call_args.kwargs["db_path"] == custom


# ---------------------------------------------------------------------------
# Returned store is not connected
# ---------------------------------------------------------------------------


def test_make_agent_store_does_not_call_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The factory must not call connect() — caller is responsible."""
    monkeypatch.delenv("FACTORY_DB", raising=False)
    _mock_json, mock_sqlite = _patch_classes(monkeypatch)

    result = factory_mod.make_agent_store()

    assert result is mock_sqlite.return_value
    instance = mock_sqlite.return_value
    instance.connect.assert_not_called()


# ---------------------------------------------------------------------------
# Invalid FACTORY_DB value falls back to AgentStore
# ---------------------------------------------------------------------------


def test_make_agent_store_invalid_lyra_db_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any FACTORY_DB value other than 'json' falls back to AgentStore."""
    monkeypatch.setenv("FACTORY_DB", "postgres")
    mock_json, mock_sqlite = _patch_classes(monkeypatch)

    result = factory_mod.make_agent_store()

    mock_json.assert_not_called()
    mock_sqlite.assert_called_once()
    assert result is mock_sqlite.return_value


# ---------------------------------------------------------------------------
# FACTORY_AGENT_STORE_PATH relative → resolved against ROXABI_FACTORY_DIR or ~/.lyra
# ---------------------------------------------------------------------------


def test_make_agent_store_relative_path_with_vault_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relative FACTORY_AGENT_STORE_PATH is NOT resolved against ROXABI_FACTORY_DIR.

    The factory computes ``_vault`` but only uses it for the default path.
    When ``FACTORY_AGENT_STORE_PATH`` is set, the raw string is passed through
    unchanged.  This test documents actual behaviour; fix needed in source.
    """
    monkeypatch.setenv("FACTORY_DB", "json")
    monkeypatch.setenv("FACTORY_AGENT_STORE_PATH", "agents.json")
    monkeypatch.setenv("ROXABI_FACTORY_DIR", "/tmp/vault")
    mock_json, _mock_sqlite = _patch_classes(monkeypatch)

    result = factory_mod.make_agent_store()

    assert result is mock_json.return_value
    # Actual behaviour: relative path is NOT resolved
    assert mock_json.call_args.kwargs["path"] == Path("agents.json")


def test_make_agent_store_relative_path_fallback_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG: relative FACTORY_AGENT_STORE_PATH with no ROXABI_FACTORY_DIR stays relative.

    Same root cause as above — ``_vault`` is computed but unused when the env
    var is explicitly set.  This test documents actual behaviour.
    """
    monkeypatch.setenv("FACTORY_DB", "json")
    monkeypatch.setenv("FACTORY_AGENT_STORE_PATH", "agents.json")
    monkeypatch.delenv("ROXABI_FACTORY_DIR", raising=False)
    mock_json, _mock_sqlite = _patch_classes(monkeypatch)

    result = factory_mod.make_agent_store()

    assert result is mock_json.return_value
    # Actual behaviour: relative path is NOT resolved to ~/.lyra
    assert mock_json.call_args.kwargs["path"] == Path("agents.json")


# ---------------------------------------------------------------------------
# ROXABI_FACTORY_DIR default path behaviour
# ---------------------------------------------------------------------------


def test_json_default_path_uses_lyra_vault_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FACTORY_DB=json + ROXABI_FACTORY_DIR set uses vault dir for default path."""
    monkeypatch.setenv("FACTORY_DB", "json")
    monkeypatch.setenv("ROXABI_FACTORY_DIR", "/tmp/vault")
    monkeypatch.delenv("FACTORY_AGENT_STORE_PATH", raising=False)
    mock_json, mock_sqlite = _patch_classes(monkeypatch)

    result = factory_mod.make_agent_store()

    mock_sqlite.assert_not_called()
    mock_json.assert_called_once()
    assert result is mock_json.return_value
    assert mock_json.call_args.kwargs["path"] == Path("/tmp/vault/agents_test.json")


def test_sqlite_default_path_uses_lyra_vault_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FACTORY_DB unset + ROXABI_FACTORY_DIR set uses vault dir for default db path."""
    monkeypatch.delenv("FACTORY_DB", raising=False)
    monkeypatch.setenv("ROXABI_FACTORY_DIR", "/tmp/vault")
    mock_json, mock_sqlite = _patch_classes(monkeypatch)

    result = factory_mod.make_agent_store()

    mock_json.assert_not_called()
    mock_sqlite.assert_called_once()
    assert result is mock_sqlite.return_value
    assert mock_sqlite.call_args.kwargs["db_path"] == Path("/tmp/vault/config.db")
