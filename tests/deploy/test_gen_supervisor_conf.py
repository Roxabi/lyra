"""Tests for deploy/gen-supervisor-conf.py command-generation behavior."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "gen-supervisor-conf.py"


def _run_dry(tmp_agents: Path) -> str:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "--agents-file", str(tmp_agents)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _write_agents(tmp_path: Path, agents: dict) -> Path:
    payload = {"schema_version": "1.0", "defaults": {}, "agents": agents}
    p = tmp_path / "agents.yml"
    p.write_text(yaml.safe_dump(payload))
    return p


def test_command_override_used_verbatim(tmp_path: Path) -> None:
    # Arrange
    p = _write_agents(
        tmp_path,
        {
            "imagecli_gen": {
                "command_override": "imagecli nats-serve",
                "priority": 200,
                "startsecs": 20,
                "nkey": "image-worker.seed",
            },
        },
    )
    # Act
    out = _run_dry(p)
    # Assert
    assert "command=imagecli nats-serve" in out
    # The run_adapter.sh fallback must NOT appear for this agent's block
    assert "run_adapter.sh imagecli_gen" not in out


def test_fallback_run_hub_for_hub_name(tmp_path: Path) -> None:
    # Arrange
    p = _write_agents(tmp_path, {"hub": {"priority": 100}})
    # Act
    out = _run_dry(p)
    # Assert
    assert "run_hub.sh" in out
    assert "imagecli nats-serve" not in out


def test_fallback_run_adapter_for_other_names(tmp_path: Path) -> None:
    # Arrange
    p = _write_agents(tmp_path, {"telegram": {"priority": 200}})
    # Act
    out = _run_dry(p)
    # Assert
    assert "run_adapter.sh telegram" in out
    assert "imagecli nats-serve" not in out
    assert "run_hub.sh" not in out
