"""Regression test for #1359 — turn-writer bootstrap path resolution.

Asserts that mkdir targets the parent of the resolved db_path, not a
hypothetical vault_dir that is never used. This guards the ReadOnly=true
rootfs case where Path.home()/.lyra doesn't exist and can't be created.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_lyra_turns_db_set_skips_vault_dir_mkdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LYRA_TURNS_DB set → mkdir targets its parent, never Path.home()/.lyra."""
    from lyra.bootstrap.standalone.worker_standalone import (
        _bootstrap_turn_writer_standalone,
    )

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "turns.db"

    # Point HOME at a nonexistent path that can't be created (read-only parent).
    unwritable_root = tmp_path / "ro"
    unwritable_root.mkdir(mode=0o555)
    monkeypatch.setenv("HOME", str(unwritable_root / "nobody"))
    monkeypatch.setenv("LYRA_TURNS_DB", str(db_path))
    monkeypatch.setenv("NATS_URL", "nats://invalid:4222")
    monkeypatch.delenv("ROXABI_FACTORY_DIR", raising=False)

    # Make the connect call fail early so we exit before NATS state is touched —
    # the only behavior under test is path resolution + mkdir.
    with patch(
        "lyra.bootstrap.standalone.worker_standalone.nats_connect",
        side_effect=ConnectionError("stub: skip NATS"),
    ):
        with pytest.raises(SystemExit):
            await _bootstrap_turn_writer_standalone({})

    assert data_dir.exists(), "db_path.parent should exist after bootstrap"
    # Path.home()/.lyra must NOT have been created — proof we skipped vault_dir mkdir.
    assert not (unwritable_root / "nobody" / ".lyra").exists()


@pytest.mark.asyncio
async def test_no_lyra_turns_db_falls_back_to_vault_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LYRA_TURNS_DB unset → mkdir targets ROXABI_FACTORY_DIR (dev-mode path)."""
    from lyra.bootstrap.standalone.worker_standalone import (
        _bootstrap_turn_writer_standalone,
    )

    vault = tmp_path / "vault"
    monkeypatch.delenv("LYRA_TURNS_DB", raising=False)
    monkeypatch.setenv("ROXABI_FACTORY_DIR", str(vault))
    monkeypatch.setenv("NATS_URL", "nats://invalid:4222")

    with patch(
        "lyra.bootstrap.standalone.worker_standalone.nats_connect",
        side_effect=ConnectionError("stub: skip NATS"),
    ):
        with pytest.raises(SystemExit):
            await _bootstrap_turn_writer_standalone({})

    assert vault.exists(), "vault dir should be created when LYRA_TURNS_DB unset"
