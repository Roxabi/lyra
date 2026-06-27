"""Tests for deploy/lib/quadlet-units.sh — quadlet_containers() function.

Covers:
  - emits exactly 20 container names from the real quadlet.toml
  - declaration order matches the expected manifest list
  - no output line ends with .container or contains whitespace
  - fail-closed when quadlet.toml contains no container declarations
  - fail-closed when quadlet.toml is missing entirely
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "deploy" / "lib" / "quadlet-units.sh"

EXPECTED_CONTAINERS = [
    "factory-nats",
    "factory-hub",
    "factory-telegram",
    "factory-discord",
    "factory-dashboard",
    "factory-clipool",
    "factory-gh-helper",
    "factory-turn-writer",
    "factory-blobstore",
    "factory-omp",
    "factory-socialmedia-adapter",
    "factory-loki",
    "factory-promtail",
    "factory-langfuse-postgres",
    "factory-langfuse-clickhouse",
    "factory-langfuse-redis",
    "factory-langfuse-minio",
    "factory-langfuse-worker",
    "factory-langfuse-web",
    "factory-otel-collector",
]


def _source_and_run(helper: Path) -> subprocess.CompletedProcess[str]:
    """Source the helper and invoke quadlet_containers(), capturing output."""
    return subprocess.run(
        ["bash", "-c", f"source {shlex.quote(str(helper))}; quadlet_containers"],
        capture_output=True,
        text=True,
    )


def test_emits_exactly_10_containers() -> None:
    """Real helper against the real quadlet.toml → returncode 0, exactly 20 names."""
    result = _source_and_run(HELPER)
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.splitlines() if line]
    assert len(lines) == 20, f"expected 20 containers, got {len(lines)}: {lines}"


def test_declaration_order() -> None:
    """The 20 lines equal EXPECTED_CONTAINERS in declaration order."""
    result = _source_and_run(HELPER)
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.splitlines() if line]
    assert lines == EXPECTED_CONTAINERS, f"order mismatch: {lines}"


def test_container_suffix_stripped() -> None:
    """No output line ends with .container and none contains whitespace."""
    result = _source_and_run(HELPER)
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.splitlines() if line]
    for line in lines:
        assert not line.endswith(".container"), f"suffix not stripped: {line!r}"
        assert " " not in line and "\t" not in line, f"whitespace in name: {line!r}"


def test_fail_closed_on_zero_components(tmp_path: Path) -> None:
    """quadlet_containers exits non-zero when quadlet.toml has no container lines."""
    lib_dir = tmp_path / "deploy" / "lib"
    lib_dir.mkdir(parents=True)
    helper_copy = lib_dir / "quadlet-units.sh"
    shutil.copy(HELPER, helper_copy)

    toml = tmp_path / "deploy" / "quadlet.toml"
    toml.write_text("[meta]\nx = 1\n")

    result = subprocess.run(
        ["bash", "-c", f"source {shlex.quote(str(helper_copy))}; quadlet_containers"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "expected non-zero exit when 0 containers parsed"
    # stdout must contain no container-shaped names
    assert not any(
        line.startswith("factory-") for line in result.stdout.splitlines()
    ), f"unexpected container names in stdout: {result.stdout!r}"


def test_fail_closed_on_missing_toml(tmp_path: Path) -> None:
    """quadlet_containers exits non-zero when quadlet.toml does not exist."""
    lib_dir = tmp_path / "deploy" / "lib"
    lib_dir.mkdir(parents=True)
    helper_copy = lib_dir / "quadlet-units.sh"
    shutil.copy(HELPER, helper_copy)

    # deliberately do NOT create tmp_path/deploy/quadlet.toml

    result = subprocess.run(
        ["bash", "-c", f"source {shlex.quote(str(helper_copy))}; quadlet_containers"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "expected non-zero exit when quadlet.toml is absent"
