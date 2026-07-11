"""Shared parsers for deploy/generated/secrets-manifest.sh — test SSoT."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_SH = REPO_ROOT / "deploy" / "generated" / "secrets-manifest.sh"


def parse_manifest_block(
    block_name: str,
    manifest: Path = MANIFEST_SH,
) -> dict[str, str]:
    """Parse a declare -A block from secrets-manifest.sh."""
    entries: dict[str, str] = {}
    in_block = False
    for line in manifest.read_text().splitlines():
        if f"{block_name}=(" in line:
            in_block = True
            continue
        if in_block:
            stripped = line.strip()
            if stripped == ")":
                break
            if stripped.startswith("[") and "]=" in stripped:
                name = stripped[1 : stripped.index("]=")]
                val = stripped[stripped.index('="') + 2 : -1]
                entries[name] = val
    return entries


def parse_manifest_sources(manifest: Path = MANIFEST_SH) -> dict[str, str]:
    """Parse SECRET_SOURCES block from secrets-manifest.sh."""
    return parse_manifest_block("SECRET_SOURCES", manifest)


def parse_manifest_policy(manifest: Path = MANIFEST_SH) -> dict[str, str]:
    """Parse SECRET_POLICY block from secrets-manifest.sh (install.sh SSoT)."""
    return parse_manifest_block("SECRET_POLICY", manifest)


def factory_nats_seeds_expected(manifest: Path = MANIFEST_SH) -> set[str]:
    """All secrets with policy=nats-seed in the committed manifest."""
    return {
        name
        for name, policy in parse_manifest_policy(manifest).items()
        if policy == "nats-seed"
    }
