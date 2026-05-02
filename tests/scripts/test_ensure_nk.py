from __future__ import annotations

from unittest.mock import patch

import pytest
from scripts._nk import ensure_nk_or_exit


def test_ensure_nk_exits_when_absent(capsys: pytest.CaptureFixture[str]) -> None:
    """When nk is not on PATH, ensure_nk_or_exit() exits 1 with install hint."""
    with patch("shutil.which", return_value=None):
        with pytest.raises(SystemExit) as exc_info:
            ensure_nk_or_exit()
        assert exc_info.value.code == 1


def test_ensure_nk_hint_contains_apt(capsys: pytest.CaptureFixture[str]) -> None:
    """Error message must mention apt install nats-tools."""
    with patch("shutil.which", return_value=None):
        with pytest.raises(SystemExit):
            ensure_nk_or_exit()
    captured = capsys.readouterr()
    assert (
        "apt install nats-tools" in captured.err
        or "apt install nats-tools" in captured.out
    )


def test_ensure_nk_hint_contains_github_url(capsys: pytest.CaptureFixture[str]) -> None:
    """Error message must mention nkeys GitHub releases URL."""
    with patch("shutil.which", return_value=None):
        with pytest.raises(SystemExit):
            ensure_nk_or_exit()
    captured = capsys.readouterr()
    assert (
        "github.com/nats-io/nkeys/releases" in captured.err
        or "github.com/nats-io/nkeys/releases" in captured.out
    )


def test_ensure_nk_passes_when_present() -> None:
    """When nk IS on PATH, ensure_nk_or_exit() returns None (no exit)."""
    with patch("shutil.which", return_value="/usr/local/bin/nk"):
        result = ensure_nk_or_exit()
        assert result is None
