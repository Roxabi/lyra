"""Tests for deploy/setup.py scaffold_ingress_toml (copy-if-absent, idempotent)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SETUP_PY = REPO_ROOT / "deploy" / "setup.py"


def _load_setup() -> Any:
    """Import deploy/setup.py by path (deploy/ is not an importable package)."""
    spec = importlib.util.spec_from_file_location("factory_setup", SETUP_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_creates_ingress_toml_from_example(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: absent target → copied from deploy/ingress.toml.example."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _load_setup().scaffold_ingress_toml(REPO_ROOT)
    target = tmp_path / ".roxabi" / "factory" / "ingress.toml"
    assert target.is_file()
    assert "connector" in target.read_text()


def test_does_not_clobber_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Idempotent: an existing operator-edited ingress.toml is left untouched."""
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / ".roxabi" / "factory" / "ingress.toml"
    target.parent.mkdir(parents=True)
    target.write_text("# operator edit\n")
    _load_setup().scaffold_ingress_toml(REPO_ROOT)
    assert target.read_text() == "# operator edit\n"


def test_skips_when_example_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No example under factory_dir → skip (no file created), no crash."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _load_setup().scaffold_ingress_toml(tmp_path)  # factory_dir has no deploy/ example
    assert not (tmp_path / ".roxabi" / "factory" / "ingress.toml").exists()
