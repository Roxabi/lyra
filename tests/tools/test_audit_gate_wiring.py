"""Tests for tools/audit_gate_wiring.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

from tools.audit_gate_wiring import audit, main


def _write_stack(root: Path, gates: dict) -> None:
    stack_dir = root / ".claude"
    stack_dir.mkdir(parents=True, exist_ok=True)
    (stack_dir / "stack.yml").write_text(
        yaml.safe_dump({"quality_gates": gates}),
        encoding="utf-8",
    )


def _write_pre_commit(root: Path, content: str) -> None:
    (root / ".pre-commit-config.yaml").write_text(content, encoding="utf-8")


def _write_ci(root: Path, content: str) -> None:
    ci_dir = root / ".github" / "workflows"
    ci_dir.mkdir(parents=True)
    (ci_dir / "ci.yml").write_text(content, encoding="utf-8")


def test_all_wired_passes(tmp_path: Path) -> None:
    _write_stack(
        tmp_path,
        {
            "folder_size": {"enabled": True, "script": "tools/check_folder_size.sh"},
            "secrets_source": {"enabled": True, "script": "tools/check_secrets_source.sh"},
        },
    )
    _write_pre_commit(tmp_path, "entry: tools/check_secrets_source.sh\n")
    _write_ci(tmp_path, "run: bash tools/check_folder_size.sh\n")

    assert audit(tmp_path) == []


def test_orphan_reported(tmp_path: Path) -> None:
    _write_stack(
        tmp_path,
        {"volumes_table": {"enabled": True, "script": "tools/check_volumes_table.sh"}},
    )
    _write_pre_commit(tmp_path, "repos: []\n")
    _write_ci(tmp_path, "jobs: {}\n")

    orphans = audit(tmp_path)
    assert len(orphans) == 1
    assert "volumes_table" in orphans[0]


def test_disabled_gate_skipped(tmp_path: Path) -> None:
    _write_stack(
        tmp_path,
        {"volumes_table": {"enabled": False, "script": "tools/check_volumes_table.sh"}},
    )
    _write_pre_commit(tmp_path, "repos: []\n")
    _write_ci(tmp_path, "jobs: {}\n")

    assert audit(tmp_path) == []


def test_main_exit_codes(tmp_path: Path) -> None:
    _write_stack(tmp_path, {})
    _write_pre_commit(tmp_path, "repos: []\n")
    _write_ci(tmp_path, "jobs: {}\n")
    assert main(["--root", str(tmp_path)]) == 0

    _write_stack(
        tmp_path,
        {"no_runtime_toml_bots": {"enabled": True, "script": "tools/check_no_runtime_toml_bots.sh"}},
    )
    assert main(["--root", str(tmp_path)]) == 1


def test_repo_root_audit_passes() -> None:
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(root / "tools" / "audit_gate_wiring.py"), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr