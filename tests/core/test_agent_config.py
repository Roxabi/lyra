"""Tests for ModelConfig dataclass (non-persona, non-conversion).

The TOML loading path (load_agent_config) was removed in #346.
Tests that exercised TOML loading have been removed; the remaining
tests cover the ModelConfig dataclass directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from lyra.core.agent_config import ModelConfig


class TestModelConfig:
    def test_defaults(self) -> None:
        cfg = ModelConfig()
        assert cfg.backend == "claude-cli"
        assert cfg.model == "claude-sonnet-4-5"
        assert cfg.max_turns is None  # None = unlimited (default)
        assert cfg.tools == ()

    def test_tools_field_is_tuple(self) -> None:
        cfg = ModelConfig(tools=("Read", "Grep"))
        assert isinstance(cfg.tools, tuple)
        assert cfg.tools == ("Read", "Grep")

    def test_frozen(self) -> None:
        cfg = ModelConfig()
        with pytest.raises(ValidationError):
            cfg.backend = "ollama"  # type: ignore[misc]

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
