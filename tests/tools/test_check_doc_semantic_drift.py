"""Tests for tools/check_doc_semantic_drift.py — Phase C semantic doc gate."""

from __future__ import annotations

import re
import sys
from io import StringIO
from pathlib import Path

from tools.check_doc_semantic_drift import RULES, main


def _pyproject(root: Path) -> None:
    root.joinpath("pyproject.toml").write_text(
        '[project]\nlicense = { text = "AGPL-3.0-or-later" }\n',
        encoding="utf-8",
    )


def _run(root: Path, args: list[str] | None = None) -> tuple[int, str]:
    buf = StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        rc = main(["--root", str(root)] + (args or []))
    finally:
        sys.stdout = old
    return rc, buf.getvalue()


def test_clean_operational_doc_passes(tmp_path: Path) -> None:
    _pyproject(tmp_path)
    (tmp_path / "README.md").write_text(
        "# Lyra\n\n[![License: AGPL](badge)](LICENSE)\n\n"
        "## License\n\nAGPL-3.0-or-later\n",
        encoding="utf-8",
    )
    doc = tmp_path / "docs" / "DEPLOYMENT.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("Nine containers. Use `make factory status`.\n", encoding="utf-8")
    rc, out = _run(tmp_path)
    assert rc == 0
    assert "OK" in out


def test_make_lyra_fails(tmp_path: Path) -> None:
    _pyproject(tmp_path)
    (tmp_path / "README.md").write_text("# Lyra\nAGPL\n", encoding="utf-8")
    doc = tmp_path / "docs" / "COMMANDS.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("Run `make lyra reload`.\n", encoding="utf-8")
    rc, out = _run(tmp_path)
    assert rc == 1
    assert "make_lyra" in out


def test_history_exempt(tmp_path: Path) -> None:
    _pyproject(tmp_path)
    (tmp_path / "README.md").write_text("# Lyra\nAGPL\n", encoding="utf-8")
    doc = tmp_path / "docs" / "history" / "old.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("make lyra stop\n", encoding="utf-8")
    rc, _ = _run(tmp_path)
    assert rc == 0


def test_semantic_ignore_exempt(tmp_path: Path) -> None:
    _pyproject(tmp_path)
    (tmp_path / "README.md").write_text("# Lyra\nAGPL\n", encoding="utf-8")
    doc = tmp_path / "docs" / "ops" / "note.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("make lyra reload <!-- semantic-ignore -->\n", encoding="utf-8")
    rc, _ = _run(tmp_path)
    assert rc == 0


def test_readme_mit_fails(tmp_path: Path) -> None:
    _pyproject(tmp_path)
    (tmp_path / "README.md").write_text(
        "[![License: MIT](badge)](LICENSE)\n\n## License\n\nMIT\n",
        encoding="utf-8",
    )
    rc, out = _run(tmp_path)
    assert rc == 1
    assert "readme" in out.lower()


def test_tombstones_all_fire(tmp_path: Path) -> None:
    """Every #2220 tombstone string trips its rule on a fresh doc."""
    _pyproject(tmp_path)
    (tmp_path / "README.md").write_text("# Lyra\nAGPL\n", encoding="utf-8")
    doc = tmp_path / "docs" / "ops" / "stale.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(
        "Run `make deploy` to ship.\n"
        "Also `make full-deploy` works.\n"
        "Register the slug in `adr/meta.json`.\n"
        "Build a `CredentialStore` instance.\n"
        "Export `FACTORY_VAULT_DIR=/tmp/x`.\n"
        "Then `systemctl reload nats` to apply.\n",
        encoding="utf-8",
    )
    rc, out = _run(tmp_path)
    assert rc == 1
    for rule_id in (
        "tombstone_make_deploy",
        "tombstone_adr_meta_json",
        "tombstone_credential_store",
        "tombstone_factory_vault_dir",
        "tombstone_systemctl_reload_nats",
    ):
        assert rule_id in out, f"expected {rule_id} in output:\n{out}"


def test_make_deploy_retired_context_is_exempt(tmp_path: Path) -> None:
    """A line documenting the retirement itself must not trip (historical keyword).

    This is exactly how the live `docs/DEPLOYMENT.md` records the #1930 removal —
    the doc is doing its job, so the gate stays quiet.
    """
    _pyproject(tmp_path)
    (tmp_path / "README.md").write_text("# Lyra\nAGPL\n", encoding="utf-8")
    doc = tmp_path / "docs" / "DEPLOYMENT.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(
        "`make deploy` / `make full-deploy` are RETIRED (#1930) — they fail fast.\n",
        encoding="utf-8",
    )
    rc, out = _run(tmp_path)
    assert rc == 0, out
    assert "tombstone_make_deploy" not in out


def test_root_agents_md_is_scanned(tmp_path: Path) -> None:
    """Root AGENTS.md is in scope (#2220 scope item 2) — a tombstone there trips."""
    _pyproject(tmp_path)
    (tmp_path / "README.md").write_text("# Lyra\nAGPL\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(
        "Config lives under `FACTORY_VAULT_DIR`.\n", encoding="utf-8"
    )
    rc, out = _run(tmp_path)
    assert rc == 1
    assert "tombstone_factory_vault_dir" in out
    assert "AGENTS.md" in out


def test_stale_container_count_message_has_no_hardcoded_count() -> None:
    """The stale_container_count fix-message must not itself hardcode a count.

    Regression for the self-referential staleness the 2026-06-30 audit caught: the
    message said "nine Quadlet containers" while the live count kept climbing, so the
    gate's own guidance became the stale fact it exists to correct. Point at the
    generated SSoT (`CURRENT.generated.md` / `deploy/quadlet.toml`) instead.
    """
    rule = next(r for r in RULES if r.rule_id == "stale_container_count")
    assert not re.search(
        r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        r"thirteen|fourteen|fifteen|sixteen)\s+(?:Quadlet\s+)?containers?\b",
        rule.message,
        re.IGNORECASE,
    ), f"fix-message must not hardcode a container count: {rule.message!r}"
