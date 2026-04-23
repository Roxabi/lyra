"""Tests for `lyra-agent validate` command.

After #346, the validate command reads from the DB only (not TOML files).
Tests seed agent rows into the DB before validating.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lyra.cli import agent_app as app
from lyra.infrastructure.stores.agent_store import AgentRow, AgentStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

runner = CliRunner()


def _seed_agent(
    db_path: Path,
    name: str = "validagent",
    backend: str = "claude-cli",
    model: str = "claude-sonnet-4-5",
    smart_routing_json: str | None = None,
) -> None:
    """Insert an AgentRow into a fresh (or existing) DB at db_path."""

    async def _run() -> None:
        store = AgentStore(db_path=db_path)
        await store.connect()
        await store.upsert(
            AgentRow(
                name=name,
                backend=backend,
                model=model,
                smart_routing_json=smart_routing_json,
            )
        )
        await store.close()

    asyncio.run(_run())


class TestValidate:
    """Tests for `lyra-agent validate`."""

    def test_valid_agent_exits_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Valid agent in DB exits 0 with no warnings."""
        # Arrange
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        _seed_agent(tmp_path / "config.db", name="validagent")

        # Act
        result = runner.invoke(app, ["validate", "validagent"])

        # Assert
        assert result.exit_code == 0, result.output
        assert "error" not in result.output.lower()
        assert "invalid" not in result.output.lower()

    @pytest.mark.parametrize("backend", ["claude-cli", "nats", "ollama"])
    def test_sr_enabled_exits_nonzero_on_any_backend(
        self, backend: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """smart_routing enabled is rejected on any backend — validator is backend-agnostic."""  # noqa: E501
        # Arrange -- any backend with smart_routing enabled should fail validation
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        _seed_agent(
            tmp_path / "config.db",
            name="mismatch",
            backend=backend,
            smart_routing_json='{"enabled": true}',
        )

        # Act
        result = runner.invoke(app, ["validate", "mismatch"])

        # Assert -- exits non-zero (error) because DB validate is strict
        assert result.exit_code != 0, result.output

    def test_nonexistent_exits_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-existent agent name exits non-zero with 'not found' message."""
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))

        # Act
        result = runner.invoke(app, ["validate", "ghost"])

        # Assert
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "ghost" in result.output.lower()
