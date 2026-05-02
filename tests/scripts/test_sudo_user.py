from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

# operator_home() doesn't exist yet — import will fail (desired RED state)
from scripts.gen_nkeys import operator_home


def test_operator_home_with_sudo_user(tmp_path: Path) -> None:
    """When SUDO_USER is set, operator_home() returns that user's home dir."""
    mock_pw = MagicMock()
    mock_pw.pw_dir = str(tmp_path / "sudo-user-home")
    with patch.dict(os.environ, {"SUDO_USER": "testuser"}, clear=False):
        with patch("pwd.getpwnam", return_value=mock_pw) as mock_getpw:
            result = operator_home()
            mock_getpw.assert_called_once_with("testuser")
            assert result == Path(str(tmp_path / "sudo-user-home"))


def test_operator_home_without_sudo_user() -> None:
    """Without SUDO_USER, operator_home() falls back to Path.home()."""
    env = {k: v for k, v in os.environ.items() if k != "SUDO_USER"}
    with patch.dict(os.environ, env, clear=True):
        result = operator_home()
        assert result == Path.home()
