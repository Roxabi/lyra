"""Tests for ModelConfig dataclass (non-persona, non-conversion).

The TOML loading path (load_agent_config) was removed in #346.
Tests that exercised TOML loading have been removed; the remaining
tests cover the ModelConfig dataclass directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from lyra.core.agent.agent_config import ModelConfig


class TestModelConfig:
    def test_defaults(self) -> None:
        cfg = ModelConfig()
        assert cfg.backend == "claude-cli"
        assert cfg.model == "claude-opus-4-6"
        assert cfg.max_turns is None  # None = unlimited (default)
        assert cfg.tools == ()

    def test_backend_litellm_rejected(self) -> None:
        from lyra.core.agent.agent_builder import _validate_backend_model

        with pytest.raises(ValueError, match="Invalid backend"):
            _validate_backend_model("litellm", "claude-opus-4-6", "test-agent")

    def test_backend_ollama_rejected(self) -> None:
        from lyra.core.agent.agent_builder import _validate_backend_model

        with pytest.raises(ValueError, match="Invalid backend"):
            _validate_backend_model("ollama", "claude-opus-4-6", "test-agent")

    def test_backend_nats_accepted(self) -> None:
        from lyra.core.agent.agent_builder import _validate_backend_model

        # must not raise
        _validate_backend_model("nats", "claude-sonnet-4-6", "test-agent")

    def test_tools_field_is_tuple(self) -> None:
        cfg = ModelConfig(tools=("Read", "Grep"))
        assert isinstance(cfg.tools, tuple)
        assert cfg.tools == ("Read", "Grep")

    def test_frozen(self) -> None:
        cfg = ModelConfig()
        with pytest.raises(ValidationError):
            setattr(cfg, "backend", "ollama")

    def test_cwd_defaults_to_none(self) -> None:
        cfg = ModelConfig()
        assert cfg.cwd is None

    def test_cwd_accepts_path(self, tmp_path: Path) -> None:
        cfg = ModelConfig(cwd=tmp_path)
        assert cfg.cwd == tmp_path

    def test_eq_ignores_cwd_difference(self) -> None:
        a = ModelConfig(cwd=Path("/a"))
        b = ModelConfig(cwd=Path("/b"))
        assert a == b

    def test_eq_detects_model_difference(self) -> None:
        a = ModelConfig(model="haiku")
        b = ModelConfig(model="opus")
        assert a != b

    def test_hash_ignores_cwd(self) -> None:
        a = ModelConfig(cwd=Path("/a"))
        b = ModelConfig(cwd=Path("/b"))
        assert hash(a) == hash(b)

    # ------------------------------------------------------------------
    # effort field — included in __eq__ and __hash__ (CliPool guard)
    # ------------------------------------------------------------------

    def test_effort_defaults_to_none(self) -> None:
        cfg = ModelConfig()
        assert cfg.effort is None

    def test_effort_accepts_valid_literal(self) -> None:
        for val in ("low", "medium", "high", "xhigh", "max"):
            cfg = ModelConfig(effort=val)  # type: ignore[arg-type]
            assert cfg.effort == val

    def test_eq_detects_effort_difference(self) -> None:
        """Configs differing only in effort must be unequal (CliPool guard regression)."""  # noqa: E501
        a = ModelConfig(effort="high")  # type: ignore[arg-type]
        b = ModelConfig(effort="low")  # type: ignore[arg-type]
        assert a != b

    def test_eq_effort_none_vs_set(self) -> None:
        a = ModelConfig(effort=None)
        b = ModelConfig(effort="medium")  # type: ignore[arg-type]
        assert a != b

    def test_hash_detects_effort_difference(self) -> None:
        """Configs differing only in effort must have different hashes (CliPool guard)."""  # noqa: E501
        a = ModelConfig(effort="high")  # type: ignore[arg-type]
        b = ModelConfig(effort="low")  # type: ignore[arg-type]
        assert hash(a) != hash(b)

    def test_hash_effort_none_vs_set(self) -> None:
        a = ModelConfig(effort=None)
        b = ModelConfig(effort="max")  # type: ignore[arg-type]
        assert hash(a) != hash(b)

    def test_eq_same_effort_equal(self) -> None:
        a = ModelConfig(effort="medium")  # type: ignore[arg-type]
        b = ModelConfig(effort="medium")  # type: ignore[arg-type]
        assert a == b

    def test_hash_same_effort_same_hash(self) -> None:
        a = ModelConfig(effort="high")  # type: ignore[arg-type]
        b = ModelConfig(effort="high")  # type: ignore[arg-type]
        assert hash(a) == hash(b)

    def test_eq_returns_not_implemented_for_non_modelconfig(self) -> None:
        """__eq__ must return NotImplemented for non-ModelConfig types."""
        cfg = ModelConfig()
        result = cfg.__eq__("not a ModelConfig")
        assert result is NotImplemented
