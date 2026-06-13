"""Tests for _export_secret_file — Podman secret-file → env-var bridge (#1881)."""

from __future__ import annotations

import pytest


def test_secret_file_is_stripped_and_exported(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """File with surrounding whitespace is stripped and exported into env."""
    from factory.bootstrap.standalone.worker_standalone import _export_secret_file

    secret_file = tmp_path / "factory-litellm-key.tok"
    secret_file.write_text("  sk-abc\n")

    monkeypatch.setenv("LITELLM_API_KEY_FILE", str(secret_file))
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)

    _export_secret_file("LITELLM_API_KEY_FILE", "LITELLM_API_KEY")

    import os

    assert os.environ["LITELLM_API_KEY"] == "sk-abc"


def test_unset_file_var_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """LITELLM_API_KEY_FILE unset → no-op, LITELLM_API_KEY is not created."""
    from factory.bootstrap.standalone.worker_standalone import _export_secret_file

    monkeypatch.delenv("LITELLM_API_KEY_FILE", raising=False)
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)

    _export_secret_file("LITELLM_API_KEY_FILE", "LITELLM_API_KEY")

    import os

    assert "LITELLM_API_KEY" not in os.environ


def test_empty_secret_file_exits(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Empty/whitespace-only secret file → SystemExit with clear message."""
    from factory.bootstrap.standalone.worker_standalone import _export_secret_file

    secret_file = tmp_path / "factory-litellm-key.tok"
    secret_file.write_text("   \n")

    monkeypatch.setenv("LITELLM_API_KEY_FILE", str(secret_file))
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        _export_secret_file("LITELLM_API_KEY_FILE", "LITELLM_API_KEY")

    assert "LITELLM_API_KEY_FILE" in str(exc_info.value)
    assert str(secret_file) in str(exc_info.value)
