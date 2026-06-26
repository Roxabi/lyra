"""Tests for deploy/lib/operator-log.sh and install.sh force-flag split."""

from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = REPO_ROOT / "deploy" / "install.sh"
OPERATOR_LOG_SH = REPO_ROOT / "deploy" / "lib" / "operator-log.sh"
MANIFEST_SH = REPO_ROOT / "deploy" / "generated" / "secrets-manifest.sh"
POLICY_TOML = REPO_ROOT / "deploy" / "secrets-policy.toml"


def _parse_manifest_sources() -> dict[str, str]:
    sources: dict[str, str] = {}
    in_sources_block = False
    for line in MANIFEST_SH.read_text().splitlines():
        if "SECRET_SOURCES=(" in line:
            in_sources_block = True
            continue
        if in_sources_block:
            stripped = line.strip()
            if stripped == ")":
                break
            if stripped.startswith("[") and "]=" in stripped:
                name = stripped[1 : stripped.index("]=")]
                val = stripped[stripped.index('="') + 2 : -1]
                sources[name] = val
    return sources


def _parse_policy() -> dict[str, str]:
    with POLICY_TOML.open("rb") as f:
        data = tomllib.load(f)
    return {name: attrs["policy"] for name, attrs in data.get("secret", {}).items()}


def _create_stub_seeds(home_tmp: Path) -> None:
    sources = _parse_manifest_sources()
    policies = _parse_policy()
    factory_data = home_tmp / ".roxabi" / "factory"
    for name, rel in sources.items():
        if rel == "n/a":
            continue
        policy = policies.get(name, "")
        if policy in ("optional", "generated"):
            continue
        dest = factory_data / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f"stub-seed-for-{name}\n")
    nkeys = factory_data / "nkeys"
    nkeys.mkdir(parents=True, exist_ok=True)
    auth = nkeys / "auth.conf"
    if not auth.exists():
        auth.write_text(
            'authorization {\n  users = [\n    { nkey: "UABC"\n'
            '      permissions: { publish: { allow: [] } subscribe: { allow: [] } } }\n  ]\n}\n'
        )


def _run_bash(script: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=merged,
        check=False,
    )


@pytest.fixture
def home_tmp(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    _create_stub_seeds(root)
    return root


def test_op_log_writes_jsonl(home_tmp: Path) -> None:
    log_path = home_tmp / ".local" / "state" / "factory" / "logs" / "operator.log"
    result = _run_bash(
        f'source "{OPERATOR_LOG_SH}" && op_log test_event action=ping exit=0',
        env={
            "HOME": str(home_tmp),
            "XDG_STATE_HOME": str(home_tmp / ".local" / "state"),
            "OPERATOR_LOG": str(log_path),
        },
    )
    assert result.returncode == 0, result.stderr
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["event"] == "test_event"
    assert row["action"] == "ping"
    assert row["exit"] == "0"
    assert "ts" in row
    assert "user" in row


def test_rotation_log_append_creates_markdown(home_tmp: Path) -> None:
    rot_path = home_tmp / ".roxabi" / "factory" / "rotation-log.md"
    op_path = home_tmp / ".local" / "state" / "factory" / "logs" / "operator.log"
    result = _run_bash(
        f'source "{OPERATOR_LOG_SH}" && rotation_log_append blobstore quarterly trigger=test',
        env={
            "HOME": str(home_tmp),
            "XDG_STATE_HOME": str(home_tmp / ".local" / "state"),
            "ROTATION_LOG": str(rot_path),
            "OPERATOR_LOG": str(op_path),
        },
    )
    assert result.returncode == 0, result.stderr
    text = rot_path.read_text()
    assert "secret:blobstore" in text
    assert "reason:quarterly" in text
    op_lines = op_path.read_text().strip().splitlines()
    assert json.loads(op_lines[-1])["event"] == "rotation_log"


def test_force_secrets_does_not_regen_existing_blobstore(home_tmp: Path) -> None:
    tok = home_tmp / ".roxabi" / "factory" / "blobstore.tok"
    tok.parent.mkdir(parents=True, exist_ok=True)
    tok.write_text("STABLE_BLOBSTORE_TOKEN\n")
    before = tok.read_text()
    result = subprocess.run(
        [str(INSTALL_SCRIPT), "--force-secrets", "--secrets-only", "--dry-run"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home_tmp)},
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[skip]" in result.stdout and "blobstore.tok" in result.stdout
    assert tok.read_text() == before


def test_force_regen_blobstore_logged(home_tmp: Path) -> None:
    tok = home_tmp / ".roxabi" / "factory" / "blobstore.tok"
    tok.parent.mkdir(parents=True, exist_ok=True)
    tok.write_text("OLD_TOKEN\n")
    log_path = home_tmp / ".local" / "state" / "factory" / "logs" / "operator.log"
    rot_path = home_tmp / ".roxabi" / "factory" / "rotation-log.md"
    result = subprocess.run(
        [str(INSTALL_SCRIPT), "--force-regen-blobstore", "--secrets-only", "--dry-run"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "HOME": str(home_tmp),
            "XDG_STATE_HOME": str(home_tmp / ".local" / "state"),
            "OPERATOR_LOG": str(log_path),
            "ROTATION_LOG": str(rot_path),
        },
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "would regenerate" in result.stdout
    assert tok.read_text() == "OLD_TOKEN\n"
    events = [json.loads(line)["event"] for line in log_path.read_text().splitlines()]
    assert "blobstore_regen" in events