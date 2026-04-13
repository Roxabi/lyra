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
        assert cfg.model == "claude-opus-4-6"
        assert cfg.max_turns is None  # None = unlimited (default)
        assert cfg.tools == ()
        assert cfg.base_url is None
        assert cfg.api_key is None

    def test_backend_litellm_accepted(self) -> None:
        cfg = ModelConfig(backend="litellm")
        assert cfg.backend == "litellm"

    def test_valid_backends_contains_litellm(self) -> None:
        from lyra.core.agent_config import _VALID_BACKENDS

        assert "litellm" in _VALID_BACKENDS

    def test_base_url_invalid_scheme_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelConfig(base_url="file:///etc/passwd")
        with pytest.raises(ValidationError):
            ModelConfig(base_url="gopher://example.com")

    def test_base_url_http_and_https_accepted(self) -> None:
        assert ModelConfig(base_url="http://localhost:11434/v1").base_url
        assert ModelConfig(base_url="https://api.example.com").base_url

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
    # base_url field
    # ------------------------------------------------------------------

    def test_base_url_defaults_to_none(self) -> None:
        cfg = ModelConfig()
        assert cfg.base_url is None

    def test_base_url_accepts_string(self) -> None:
        cfg = ModelConfig(base_url="http://localhost:11434/v1")
        assert cfg.base_url == "http://localhost:11434/v1"

    def test_base_url_serializes_correctly(self) -> None:
        cfg = ModelConfig(base_url="http://localhost:11434/v1")
        dumped = cfg.model_dump()
        assert dumped["base_url"] == "http://localhost:11434/v1"

    def test_base_url_roundtrips_via_model_validate(self) -> None:
        cfg = ModelConfig(base_url="http://localhost:11434/v1")
        restored = ModelConfig.model_validate(cfg.model_dump())
        assert restored.base_url == "http://localhost:11434/v1"

    def test_eq_same_base_url_are_equal(self) -> None:
        a = ModelConfig(base_url="http://localhost:11434/v1")
        b = ModelConfig(base_url="http://localhost:11434/v1")
        assert a == b

    def test_eq_different_base_url_are_not_equal(self) -> None:
        a = ModelConfig(base_url="http://localhost:11434/v1")
        b = ModelConfig(base_url="http://remotehost:8080/v1")
        assert a != b

    def test_eq_one_base_url_none_not_equal(self) -> None:
        a = ModelConfig(base_url=None)
        b = ModelConfig(base_url="http://localhost:11434/v1")
        assert a != b

    def test_hash_same_base_url_same_hash(self) -> None:
        a = ModelConfig(base_url="http://localhost:11434/v1")
        b = ModelConfig(base_url="http://localhost:11434/v1")
        assert hash(a) == hash(b)

    # ------------------------------------------------------------------
    # api_key field — excluded from hash and eq
    # ------------------------------------------------------------------

    def test_api_key_defaults_to_none(self) -> None:
        cfg = ModelConfig()
        assert cfg.api_key is None

    def test_api_key_accepts_string(self) -> None:
        cfg = ModelConfig(api_key="sk-secret")
        assert cfg.api_key == "sk-secret"

    def test_api_key_excluded_from_eq(self) -> None:
        """Two configs differing only in api_key must be equal."""
        a = ModelConfig(api_key="sk-one")
        b = ModelConfig(api_key="sk-two")
        assert a == b

    def test_api_key_excluded_from_hash(self) -> None:
        """Two configs differing only in api_key must share the same hash."""
        a = ModelConfig(api_key="sk-one")
        b = ModelConfig(api_key="sk-two")
        assert hash(a) == hash(b)

    def test_api_key_none_and_set_same_hash(self) -> None:
        a = ModelConfig(api_key=None)
        b = ModelConfig(api_key="sk-any")
        assert hash(a) == hash(b)

    def test_api_key_excluded_from_model_dump(self) -> None:
        """api_key must never leak into serialized payloads (NATS, DB, logs)."""
        cfg = ModelConfig(api_key="sk-secret")
        dumped = cfg.model_dump()
        assert "api_key" not in dumped

    def test_api_key_excluded_from_repr(self) -> None:
        """repr must not leak the credential in log/debug output."""
        cfg = ModelConfig(api_key="sk-secret")
        assert "sk-secret" not in repr(cfg)

    def test_api_key_roundtrip_does_not_restore_value(self) -> None:
        """model_dump → model_validate does not round-trip api_key by design."""
        cfg = ModelConfig(api_key="sk-secret")
        restored = ModelConfig.model_validate(cfg.model_dump())
        assert restored.api_key is None
