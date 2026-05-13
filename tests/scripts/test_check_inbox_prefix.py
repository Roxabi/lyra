"""Tests for scripts/check_inbox_prefix.py — #1040.

Covers:
  - Happy path: clean source tree exits 0, prints OK
  - Violation path (f-string): exits 0, prints FAIL line
  - Violation path (literal constant): exits 0, prints FAIL line
  - Missing --src directory: exits 1 (scanner failure, not violation)
  - Test files are excluded from scanning

Exit-code contract (audit-exit-codes rule):
  Exit 0 = scan ran OK  (violations reported on stdout, NOT as exit code)
  Exit 1 = scanner itself broke (bad path, I/O error)
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = REPO_ROOT / "scripts" / "check_inbox_prefix.py"


def _run(src_dirs: list[Path] | None = None) -> subprocess.CompletedProcess[str]:
    """Run the CLI, optionally scoped to explicit --src directories."""
    cmd = [sys.executable, str(CLI)]
    if src_dirs is not None:
        for d in src_dirs:
            cmd += ["--src", str(d)]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def _write_py(tmp_path: Path, name: str, content: str) -> Path:
    """Write a Python source file inside *tmp_path* and return its path."""
    p = tmp_path / name
    p.write_text(textwrap.dedent(content))
    return p


# ---------------------------------------------------------------------------
# Happy path — no violations
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_clean_file_exits_0(self, tmp_path: Path) -> None:
        """A file with no inbox_prefix constructions must exit 0."""
        _write_py(
            tmp_path,
            "clean.py",
            """\
            def connect():
                return nats_connect(identity_name="hub")
            """,
        )
        result = _run([tmp_path])
        assert result.returncode == 0, (
            f"Expected exit 0 for clean source.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_clean_file_prints_ok(self, tmp_path: Path) -> None:
        """OK summary must appear on stdout when no violations found."""
        _write_py(tmp_path, "ok.py", "x = 1\n")
        result = _run([tmp_path])
        assert "OK:" in result.stdout

    def test_empty_directory_exits_0(self, tmp_path: Path) -> None:
        """An empty source directory produces exit 0 with OK message."""
        result = _run([tmp_path])
        assert result.returncode == 0
        assert "OK:" in result.stdout

    def test_fixture_pair_clean_then_bad(self, tmp_path: Path) -> None:
        """Falsification guard: scanner exits 0 on clean, 1 on known-bad fixture.

        Pair-based: if either ``_check_fstring`` or ``_check_literal`` is
        silently removed, the bad-fixture half of this test will exit 0 and
        fail. Replaces the prior repo-state-dependent
        ``test_prod_source_exits_0`` which only proved that prod source
        happens to be clean today.
        """
        clean = tmp_path / "clean"
        bad = tmp_path / "bad"
        clean.mkdir()
        bad.mkdir()
        _write_py(
            clean,
            "ok.py",
            """\
            def connect():
                return nats_connect(identity_name="hub")
            """,
        )
        _write_py(
            bad,
            "violation.py",
            """\
            def connect(name):
                return nats_connect(inbox_prefix=f"_INBOX.{name}")
            """,
        )

        clean_result = _run([clean])
        assert clean_result.returncode == 0, (
            f"Clean fixture must exit 0.\nstdout: {clean_result.stdout}"
        )
        assert "OK:" in clean_result.stdout

        bad_result = _run([bad])
        assert bad_result.returncode == 1, (
            f"Known-bad fixture must exit 1 — detection logic broken.\n"
            f"stdout: {bad_result.stdout}"
        )
        assert "FAIL" in bad_result.stdout


# ---------------------------------------------------------------------------
# Violation path — f-string construction
# ---------------------------------------------------------------------------


class TestFstringViolation:
    def test_fstring_violation_exits_1(self, tmp_path: Path) -> None:
        """A file with f-string inbox_prefix must exit 1 (CI gate signal)."""
        _write_py(
            tmp_path,
            "bad.py",
            """\
            def connect(name):
                return nats_connect(inbox_prefix=f"_INBOX.{name}")
            """,
        )
        result = _run([tmp_path])
        assert result.returncode == 1

    def test_fstring_violation_prints_fail(self, tmp_path: Path) -> None:
        """FAIL line must appear on stdout for f-string inbox_prefix construction."""
        _write_py(
            tmp_path,
            "bad.py",
            """\
            def connect(name):
                return nats_connect(inbox_prefix=f"_INBOX.{name}")
            """,
        )
        result = _run([tmp_path])
        assert "FAIL" in result.stdout

    def test_fstring_violation_names_file(self, tmp_path: Path) -> None:
        """FAIL output must name the offending file."""
        src = _write_py(
            tmp_path,
            "named.py",
            """\
            def connect(name):
                return nats_connect(inbox_prefix=f"_INBOX.{name}")
            """,
        )
        result = _run([tmp_path])
        assert "named.py" in result.stdout or str(src) in result.stdout

    def test_fstring_single_quote_detected(self, tmp_path: Path) -> None:
        """Single-quoted f-string variant must also be flagged."""
        _write_py(
            tmp_path,
            "sq.py",
            """\
            def connect(name):
                return nats_connect(inbox_prefix=f'_INBOX.{name}')
            """,
        )
        result = _run([tmp_path])
        assert "FAIL" in result.stdout


# ---------------------------------------------------------------------------
# Violation path — literal constant (AST)
# ---------------------------------------------------------------------------


class TestLiteralViolation:
    def test_literal_violation_exits_1(self, tmp_path: Path) -> None:
        """A file with literal inbox_prefix must exit 1 (CI gate signal)."""
        _write_py(
            tmp_path,
            "literal.py",
            """\
            def connect():
                return nats_connect(inbox_prefix="_INBOX.hub")
            """,
        )
        result = _run([tmp_path])
        assert result.returncode == 1

    def test_literal_violation_prints_fail(self, tmp_path: Path) -> None:
        """FAIL line must appear on stdout for literal inbox_prefix construction."""
        _write_py(
            tmp_path,
            "literal.py",
            """\
            def connect():
                return nats_connect(inbox_prefix="_INBOX.hub")
            """,
        )
        result = _run([tmp_path])
        assert "FAIL" in result.stdout

    def test_literal_violation_names_file_and_line(self, tmp_path: Path) -> None:
        """FAIL output must include file path AND line number in <path>:<line>: form."""
        src = _write_py(
            tmp_path,
            "lit_named.py",
            """\
            def connect():
                return nats_connect(inbox_prefix="_INBOX.hub")
            """,
        )
        result = _run([tmp_path])
        combined = result.stdout + result.stderr
        # Exact format emitted by _check_literal: f"{path}:{lineno}: inbox_prefix=..."
        # The violation is on line 2 (line 1 is "def connect():").
        assert f"{src}:2:" in combined, (
            f"Expected exact '<path>:2:' substring in output.\n"
            f"src: {src}\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_literal_in_comment_not_flagged(self, tmp_path: Path) -> None:
        """inbox_prefix in a comment must NOT be flagged (AST ignores comments)."""
        _write_py(
            tmp_path,
            "comment.py",
            """\
            # inbox_prefix="_INBOX.hub" is the old approach
            def connect():
                return nats_connect(identity_name="hub")
            """,
        )
        result = _run([tmp_path])
        assert "FAIL" not in result.stdout

    def test_literal_in_docstring_not_flagged(self, tmp_path: Path) -> None:
        """inbox_prefix inside a docstring must NOT be flagged."""
        _write_py(
            tmp_path,
            "docstring.py",
            '''\
            def connect():
                """Do not use inbox_prefix="_INBOX.hub" directly."""
                return nats_connect(identity_name="hub")
            ''',
        )
        result = _run([tmp_path])
        assert "FAIL" not in result.stdout


# ---------------------------------------------------------------------------
# Test-file exclusion
# ---------------------------------------------------------------------------


class TestExclusion:
    def test_test_files_excluded(self, tmp_path: Path) -> None:
        """Files named test_*.py must be excluded from scanning."""
        _write_py(
            tmp_path,
            "test_something.py",
            """\
            def test_raw_prefix():
                # intentional raw param test
                nats_connect(inbox_prefix="_INBOX.test")
            """,
        )
        result = _run([tmp_path])
        assert "FAIL" not in result.stdout
        assert result.returncode == 0

    def test_conftest_excluded(self, tmp_path: Path) -> None:
        """conftest.py must be excluded from scanning."""
        _write_py(
            tmp_path,
            "conftest.py",
            """\
            import pytest

            @pytest.fixture
            def raw_inbox():
                return nats_connect(inbox_prefix="_INBOX.fixture")
            """,
        )
        result = _run([tmp_path])
        assert "FAIL" not in result.stdout
        assert result.returncode == 0

    def test_tests_subdir_excluded(self, tmp_path: Path) -> None:
        """Files under a tests/ subdirectory must be excluded."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        _write_py(
            tests_dir,
            "helper.py",
            """\
            def make_raw():
                return nats_connect(inbox_prefix="_INBOX.test")
            """,
        )
        result = _run([tmp_path])
        assert "FAIL" not in result.stdout
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Missing --src target
# ---------------------------------------------------------------------------


class TestMissingSrcTarget:
    def test_nonexistent_src_exits_1(self, tmp_path: Path) -> None:
        """Passing a nonexistent --src directory must exit 1 (scanner failure)."""
        missing = tmp_path / "does_not_exist"
        result = _run([missing])
        assert result.returncode == 1

    def test_nonexistent_src_error_on_stderr(self, tmp_path: Path) -> None:
        """Error for missing --src must appear on stderr."""
        missing = tmp_path / "does_not_exist"
        result = _run([missing])
        assert "ERROR" in result.stderr or "not found" in result.stderr
