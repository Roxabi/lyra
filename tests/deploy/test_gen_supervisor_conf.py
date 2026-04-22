"""Tests for deploy/gen-supervisor-conf.py command-generation behavior."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "gen-supervisor-conf.py"


def _run_dry(tmp_agents: Path, *, expect_fail: bool = False) -> str:
    """Run the generator in dry-run mode against a tmp agents.yml.

    On unexpected failure, surfaces the script's stderr/stdout via
    ``pytest.fail`` — raw ``CalledProcessError`` swallows the diagnostic
    which makes the first test run on a broken generator very hard to
    triage. 10 s timeout guards against --dry-run ever growing a blocking
    call that deadlocks CI.
    """
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "--agents-file", str(tmp_agents)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if expect_fail:
        assert result.returncode != 0, (
            f"Expected failure but exit=0; stderr={result.stderr}"
        )
        return result.stderr
    if result.returncode != 0:
        import pytest as _pytest

        _pytest.fail(
            f"gen-supervisor-conf.py failed (exit={result.returncode}):\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
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


# T4 — explicit role: happy paths


def test_resolve_role_explicit_hub(tmp_path: Path) -> None:
    # Arrange
    p = _write_agents(tmp_path, {"hub": {"role": "hub", "priority": 100}})
    # Act
    out = _run_dry(p)
    # Assert
    assert "run_hub.sh" in out


def test_resolve_role_explicit_lyra_adapter(tmp_path: Path) -> None:
    # Arrange
    p = _write_agents(tmp_path, {"telegram": {"role": "lyra-adapter", "priority": 200}})
    # Act
    out = _run_dry(p)
    # Assert
    assert "run_adapter.sh telegram" in out


def test_resolve_role_explicit_external_satellite(tmp_path: Path) -> None:
    # Arrange
    p = _write_agents(
        tmp_path,
        {
            "sat": {
                "role": "external-satellite",
                "command_override": "foo bar",
                "priority": 200,
            }
        },
    )
    # Act
    out = _run_dry(p)
    # Assert
    assert "command=foo bar" in out


# T5 — error cases


def test_resolve_role_raises_on_unknown_value(tmp_path: Path) -> None:
    # Arrange
    p = _write_agents(tmp_path, {"x": {"role": "adapter"}})
    # Act
    err = _run_dry(p, expect_fail=True)
    # Assert
    assert "unknown role 'adapter'" in err and "agent 'x'" in err


def test_resolve_role_raises_external_satellite_without_override(
    tmp_path: Path,
) -> None:
    # Arrange
    p = _write_agents(tmp_path, {"sat": {"role": "external-satellite"}})
    # Act
    err = _run_dry(p, expect_fail=True)
    # Assert
    assert "requires command_override" in err and "agent 'sat'" in err


def test_resolve_role_raises_lyra_adapter_with_override(tmp_path: Path) -> None:
    # Arrange
    p = _write_agents(
        tmp_path,
        {"telegram": {"role": "lyra-adapter", "command_override": "x"}},
    )
    # Act
    err = _run_dry(p, expect_fail=True)
    # Assert
    assert "must not set command_override" in err and "agent 'telegram'" in err


def test_resolve_role_raises_hub_on_wrong_name(tmp_path: Path) -> None:
    # Arrange
    p = _write_agents(tmp_path, {"side_hub": {"role": "hub"}})
    # Act
    err = _run_dry(p, expect_fail=True)
    # Assert
    assert "role=hub requires name 'hub'" in err and "got 'side_hub'" in err


def test_resolve_role_raises_hub_with_override(tmp_path: Path) -> None:
    # Parity with test_resolve_role_raises_lyra_adapter_with_override:
    # the hub branch dispatches to run_hub.sh and never honors a stray
    # `command_override`, so the entry is rejected at validation time.
    # Arrange
    p = _write_agents(
        tmp_path,
        {"hub": {"role": "hub", "command_override": "x"}},
    )
    # Act
    err = _run_dry(p, expect_fail=True)
    # Assert
    assert "role=hub must not set command_override" in err and "agent 'hub'" in err


# T6 — inference when role is absent


def test_resolve_role_infers_hub_from_name(tmp_path: Path) -> None:
    # Arrange
    p = _write_agents(tmp_path, {"hub": {"priority": 100}})
    # Act
    out = _run_dry(p)
    # Assert — inference routes to the hub launcher and nothing else.
    assert "run_hub.sh" in out
    assert "run_adapter.sh" not in out
    assert "command=" in out


def test_resolve_role_infers_lyra_adapter_default(tmp_path: Path) -> None:
    # Arrange
    p = _write_agents(tmp_path, {"telegram": {"priority": 200}})
    # Act
    out = _run_dry(p)
    # Assert — inference routes to the adapter launcher with the agent name,
    # never to the hub launcher.
    assert "run_adapter.sh telegram" in out
    assert "run_hub.sh" not in out
    assert "command=" in out


def test_resolve_role_infers_external_satellite_from_command_override(
    tmp_path: Path,
) -> None:
    # Arrange
    p = _write_agents(
        tmp_path,
        {"sat": {"command_override": "foo bar", "priority": 200}},
    )
    # Act
    out = _run_dry(p)
    # Assert — inference picks the override verbatim; neither the hub nor
    # adapter launcher may appear on the command line.
    assert "command=foo bar" in out
    assert "run_adapter.sh" not in out
    assert "run_hub.sh" not in out


# T7 — validate_command_override pass-through


def test_command_override_validation_chain_still_enforced(tmp_path: Path) -> None:
    # A valid role but a garbage command_override must still raise via
    # validate_command_override — the refactor must not bypass that guard.
    # Arrange
    p = _write_agents(
        tmp_path,
        {"sat": {"role": "external-satellite", "command_override": "foo; rm -rf /"}},
    )
    # Act
    err = _run_dry(p, expect_fail=True)
    # Assert
    assert "Invalid command_override" in err and "'sat'" in err


# Agent-name validation (defense-in-depth, same allowlist style as
# validate_env_key / validate_command_override).


def test_invalid_agent_name_raises(tmp_path: Path) -> None:
    # Arrange — name with a shell metachar would land on the command= line
    # for a lyra-adapter agent via `run_adapter.sh <name>`.
    p = _write_agents(tmp_path, {"telegram; rm -rf /": {"role": "lyra-adapter"}})
    # Act
    err = _run_dry(p, expect_fail=True)
    # Assert
    assert "Invalid agent name" in err and "'telegram; rm -rf /'" in err
