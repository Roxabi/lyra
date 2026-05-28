"""Security tests for audio attachment path validation (#1447).

Covers traversal vectors:
  - Dot-dot traversal
  - Absolute path outside temp directory
  - Symlink to outside temp directory
  - Symlink inside temp directory
  - Non-regular file (directory)
  - Non-existent file
  - File not owned by current user
  - Safe read_bytes with O_NOFOLLOW
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from lyra.agents.simple_agent_prompts import (
    _safe_read_bytes,
    _validate_audio_path,
)


class TestValidateAudioPath:
    """Traversal vector tests for _validate_audio_path."""

    def test_dotdot_traversal_rejected(self) -> None:
        """Paths containing '..' in any component must be rejected."""
        with pytest.raises(ValueError, match="traversal"):
            _validate_audio_path(Path("/tmp/foo/../etc/passwd"))

    def test_absolute_path_outside_temp_rejected(self) -> None:
        """Absolute paths outside the temp directory must be rejected."""
        with pytest.raises(ValueError, match="outside temp"):
            _validate_audio_path(Path("/etc/passwd"))

    def test_symlink_to_outside_temp_rejected(self) -> None:
        """Symlinks pointing outside /tmp must be rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.write_text("data")
            link = Path(tmp) / "link"
            link.symlink_to(target)
            # Even though target is inside the same tmp dir, the symlink itself
            # is rejected by the is_symlink() check.
            with pytest.raises(ValueError, match="symlinks"):
                _validate_audio_path(link)

    def test_symlink_inside_temp_rejected(self) -> None:
        """Symlinks pointing inside /tmp must still be rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.write_text("data")
            link = Path(tmp) / "link"
            link.symlink_to(target)
            with pytest.raises(ValueError, match="symlinks"):
                _validate_audio_path(link)

    def test_directory_rejected(self) -> None:
        """Directories are not regular files and must be rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(ValueError, match="not a regular file"):
                _validate_audio_path(Path(tmp))

    def test_non_existent_file_rejected(self) -> None:
        """Non-existent paths must be rejected (not a regular file)."""
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.ogg"
            with pytest.raises(ValueError, match="not a regular file"):
                _validate_audio_path(missing)

    def test_wrong_owner_rejected(self) -> None:
        """Files not owned by the current user must be rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.write_text("data")
            with patch("os.getuid", return_value=99999):
                with pytest.raises(ValueError, match="not owned by current user"):
                    _validate_audio_path(target)

    def test_valid_path_accepted(self) -> None:
        """A regular file inside the temp directory owned by current user passes."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.ogg"
            target.write_text("data")
            result = _validate_audio_path(target)
            assert result == target.resolve()


class TestSafeReadBytes:
    """Tests for _safe_read_bytes (O_NOFOLLOW)."""

    def test_reads_regular_file(self) -> None:
        """Should read bytes from a regular file."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"hello audio")
            path = Path(f.name)

        assert _safe_read_bytes(path) == b"hello audio"
        path.unlink()

    def test_rejects_symlink(self) -> None:
        """O_NOFOLLOW prevents reading through symlinks."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.write_text("data")
            link = Path(tmp) / "link"
            link.symlink_to(target)
            with pytest.raises(OSError):
                _safe_read_bytes(link)
