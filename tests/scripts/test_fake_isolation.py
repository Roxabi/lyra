"""Verify-tests for fake isolation refactor (#1093, N7).

Three unconditional guards — no @skip, no xfail — run in the default
`uv run pytest tests/scripts/` suite.

(a) prod-path guard   — scripts/_modes.py must contain no env-branch remnants
(b) contract guard    — .importlinter tests-fakes-isolation must have no ignore_imports
(c) planted-violation — proves lint-imports *actually* catches a scripts→tests import
"""

from __future__ import annotations

import configparser
import os
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── (a) prod-path guard ───────────────────────────────────────────────────────


def test_modes_has_no_env_branch_or_fake_import() -> None:
    """Regression guard: scripts/_modes.py must not contain the retired env-branch.

    Deleted in this PR (N2): the NKEY_PROVIDER env read, the env_provider variable,
    and the `from tests.fakes` lazy import.  Any reintroduction trips this guard.
    """
    source = (REPO_ROOT / "scripts" / "_modes.py").read_text()

    forbidden = ["NKEY_PROVIDER", "env_provider", "tests.fakes"]
    for term in forbidden:
        assert term not in source, (
            f"scripts/_modes.py contains retired term {term!r}. "
            "The env-branch or fake import was reintroduced — remove it."
        )


# ── (b) contract guard ────────────────────────────────────────────────────────


def test_importlinter_contract_has_no_ignore_imports() -> None:
    """Regression guard: tests-fakes-isolation contract must have no ignore_imports.

    The exemption `scripts._modes -> tests.fakes.nkey_provider` was removed in
    this PR (N6).  Any re-addition would silently bypass the isolation guarantee.
    Checks only the target contract block — not other contracts that legitimately
    carry ignore_imports.
    """
    config_path = REPO_ROOT / ".importlinter"
    config_text = config_path.read_text()

    # Parse with configparser, scoped to the target section.
    parser = configparser.ConfigParser()
    parser.read_string(config_text)

    section = "importlinter:contract:tests-fakes-isolation"
    assert parser.has_section(section), (
        f".importlinter is missing [{section}] — was it renamed or deleted?"
    )

    # No ignore_imports key in this block.
    assert not parser.has_option(section, "ignore_imports"), (
        "tests-fakes-isolation contract has an ignore_imports entry. "
        "This re-opens the isolation hole — remove it."
    )

    # forbidden_modules must include 'tests' (the broad ban, not just tests.fakes).
    forbidden_modules_raw = parser.get(section, "forbidden_modules", fallback="")
    forbidden_entries = {
        e.strip() for e in forbidden_modules_raw.splitlines() if e.strip()
    }
    assert "tests" in forbidden_entries, (
        f"tests-fakes-isolation.forbidden_modules does not include 'tests'. "
        f"Got: {forbidden_entries!r}. The contract must ban all tests.* imports."
    )


# ── (c) planted-violation ────────────────────────────────────────────────────


def test_lint_imports_catches_scripts_importing_tests(tmp_path: Path) -> None:
    """Planted-violation: proves lint-imports actually rejects a scripts→tests import.

    Builds a minimal stub tree in tmp_path so grimp only needs to resolve two tiny
    packages (scripts + tests).  Runs lint-imports with cwd=tmp_path (so the stub
    packages resolve via sys.path cwd prepend) and PYTHONPATH="" (so the real
    repo packages cannot shadow the stubs).

    The contract MUST fail — if it exits 0, the mechanism is broken.
    """
    # ── stub tree ──────────────────────────────────────────────────────────────
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "__init__.py").write_text("")
    (tmp_path / "scripts" / "_violation.py").write_text(
        "from tests.fakes import nkey_provider\n"
    )

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "__init__.py").write_text("")
    (tmp_path / "tests" / "fakes").mkdir()
    (tmp_path / "tests" / "fakes" / "__init__.py").write_text("")
    (tmp_path / "tests" / "fakes" / "nkey_provider.py").write_text("")

    # ── minimal importlinter config ───────────────────────────────────────────
    # root_packages = scripts + tests ONLY (not lyra — keeps grimp graph tiny
    # and avoids resolving the real tree).
    # source_roots intentionally omitted — import-linter does not add it to
    # sys.path; cwd=tmp_path is the correct resolution mechanism.
    config = textwrap.dedent("""\
        [importlinter]
        root_packages =
            scripts
            tests

        [importlinter:contract:no-fakes]
        name = planted violation
        type = forbidden
        source_modules =
            scripts
        forbidden_modules =
            tests
    """)
    (tmp_path / ".importlinter").write_text(config)

    # ── run lint-imports in the stub cwd ──────────────────────────────────────
    # PYTHONPATH="" prevents the real repo's scripts/tests packages from
    # shadowing the stubs.  cwd=tmp_path prepends tmp_path to sys.path so
    # find_spec("scripts") resolves to the stub.
    env = {**os.environ, "PYTHONPATH": ""}
    result = subprocess.run(
        ["lint-imports", "--config", str(tmp_path / ".importlinter")],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
    )

    combined = result.stdout + result.stderr
    assert result.returncode != 0, (
        f"lint-imports exited 0 — planted violation was NOT detected.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "_violation" in combined, (
        f"lint-imports exited {result.returncode}; '_violation' not in output.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
