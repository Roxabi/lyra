"""Tests for scripts/check_subject_literals.py — #1533.

Covers:
  - Happy path: clean source (declared subjects + module/logger names) exits 0
  - Orphan path: an undeclared lyra.* subject literal exits 1 and is listed
  - Falsification pair: clean exits 0, known-orphan exits 1 (detection alive)
  - False-positive filters: f-string fragments, getLogger() args, filenames
  - Module/logger names that resolve via the oracle are not flagged
  - Allowlist suppresses a baselined orphan
  - Test files are excluded from scanning
  - Missing --src directory exits 1 (scanner failure, not a violation)

Exit-code contract (mirrors sibling scanners):
  Exit 0 = scan ran, no orphan subjects
  Exit 1 = orphan subject(s) found OR scanner failure (bad path)
  Exit 2 = oracle hit a SyntaxError in scanned source

The oracle builds its subject inventory from the real repo root (default --root),
so declared subjects like ``lyra.turns.write`` resolve live while fabricated ones
like ``lyra.totally.fake.orphan`` are orphans.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = REPO_ROOT / "scripts" / "check_subject_literals.py"

# A fabricated subject the oracle cannot resolve (kind=subject, exists=False).
ORPHAN = "lyra.totally.fake.orphan"
# A subject that IS declared in acl-matrix.json (resolves live).
DECLARED = "lyra.turns.write"


def _run(
    src_dirs: list[Path] | None = None,
    allowlist: Path | None = None,
    root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the CLI, optionally scoped to explicit --src dirs and an allowlist."""
    cmd = [sys.executable, str(CLI)]
    if src_dirs is not None:
        for d in src_dirs:
            cmd += ["--src", str(d)]
    if allowlist is not None:
        cmd += ["--allowlist", str(allowlist)]
    if root is not None:
        cmd += ["--root", str(root)]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def _write_py(directory: Path, name: str, content: str) -> Path:
    """Write a dedented Python source file inside *directory*."""
    p = directory / name
    p.write_text(textwrap.dedent(content))
    return p


def _empty_allowlist(tmp_path: Path) -> Path:
    """Return an allowlist file with no entries (isolate from the repo default)."""
    p = tmp_path / "empty_allowlist.txt"
    p.write_text("# none\n")
    return p


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_clean_file_exits_0(self, tmp_path: Path) -> None:
        """Only a declared subject → exit 0, OK message."""
        _write_py(tmp_path, "clean.py", f'SUBJECT = "{DECLARED}"\n')
        result = _run([tmp_path], allowlist=_empty_allowlist(tmp_path))
        assert result.returncode == 0, result.stdout + result.stderr
        assert "OK" in result.stdout

    def test_empty_directory_exits_0(self, tmp_path: Path) -> None:
        """No source files → exit 0."""
        result = _run([tmp_path], allowlist=_empty_allowlist(tmp_path))
        assert result.returncode == 0
        assert "OK" in result.stdout

    def test_repo_src_passes_with_default_allowlist(self) -> None:
        """The real src/ tree passes with the committed allowlist (gate is green)."""
        result = _run()
        assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Orphan detection + falsification pair
# ---------------------------------------------------------------------------


class TestOrphanDetection:
    def test_orphan_exits_1(self, tmp_path: Path) -> None:
        _write_py(tmp_path, "bad.py", f'SUBJECT = "{ORPHAN}"\n')
        result = _run([tmp_path], allowlist=_empty_allowlist(tmp_path))
        assert result.returncode == 1, result.stdout

    def test_orphan_listed_in_output(self, tmp_path: Path) -> None:
        _write_py(tmp_path, "bad.py", f'SUBJECT = "{ORPHAN}"\n')
        result = _run([tmp_path], allowlist=_empty_allowlist(tmp_path))
        assert ORPHAN in result.stdout
        assert "FAIL" in result.stdout

    def test_orphan_names_file_and_line(self, tmp_path: Path) -> None:
        src = _write_py(tmp_path, "named.py", f'\nSUBJECT = "{ORPHAN}"\n')
        result = _run([tmp_path], allowlist=_empty_allowlist(tmp_path))
        assert f"{src}:2" in result.stdout

    def test_falsification_pair(self, tmp_path: Path) -> None:
        """Clean fixture exits 0, known-orphan fixture exits 1 — detection alive."""
        clean = tmp_path / "clean"
        bad = tmp_path / "bad"
        clean.mkdir()
        bad.mkdir()
        allow = _empty_allowlist(tmp_path)
        _write_py(clean, "ok.py", f'SUBJECT = "{DECLARED}"\n')
        _write_py(bad, "violation.py", f'SUBJECT = "{ORPHAN}"\n')

        assert _run([clean], allowlist=allow).returncode == 0
        assert _run([bad], allowlist=allow).returncode == 1


# ---------------------------------------------------------------------------
# False-positive filters
# ---------------------------------------------------------------------------


class TestFalsePositiveFilters:
    def test_fstring_fragment_not_flagged(self, tmp_path: Path) -> None:
        """A dynamic f-string subject yields a fragment that must not be flagged."""
        _write_py(
            tmp_path,
            "fstr.py",
            """\
            def subject(bot):
                return f"lyra.nonexistent.{bot}.tail"
            """,
        )
        result = _run([tmp_path], allowlist=_empty_allowlist(tmp_path))
        assert result.returncode == 0, result.stdout

    def test_getlogger_arg_not_flagged(self, tmp_path: Path) -> None:
        """A logger name passed to getLogger() must not be flagged as a subject."""
        _write_py(
            tmp_path,
            "log.py",
            """\
            import logging
            log = logging.getLogger("lyra.nonexistent.logger")
            """,
        )
        result = _run([tmp_path], allowlist=_empty_allowlist(tmp_path))
        assert result.returncode == 0, result.stdout

    def test_filename_literal_not_flagged(self, tmp_path: Path) -> None:
        """A filename-shaped literal (lyra.<ext>) must not be flagged."""
        _write_py(tmp_path, "fname.py", 'PATH = "lyra.toml"\n')
        result = _run([tmp_path], allowlist=_empty_allowlist(tmp_path))
        assert result.returncode == 0, result.stdout

    def test_module_path_not_flagged(self, tmp_path: Path) -> None:
        """A real module path resolves as kind=module, never an orphan subject."""
        _write_py(tmp_path, "mod.py", 'NAME = "lyra.commands"\n')
        result = _run([tmp_path], allowlist=_empty_allowlist(tmp_path))
        assert result.returncode == 0, result.stdout

    def test_comment_not_flagged(self, tmp_path: Path) -> None:
        """Comments are not AST nodes — an orphan in a comment is not flagged."""
        _write_py(
            tmp_path,
            "comment.py",
            f"""\
            # {ORPHAN} mentioned in a comment only
            def f():
                return 1
            """,
        )
        result = _run([tmp_path], allowlist=_empty_allowlist(tmp_path))
        assert result.returncode == 0, result.stdout

    def test_docstring_literal_is_flagged(self, tmp_path: Path) -> None:
        """A bare orphan string literal used as a docstring IS an AST Constant and
        IS scanned — the scanner does not special-case docstrings."""
        _write_py(
            tmp_path,
            "docstring.py",
            f'''\
            def f():
                "{ORPHAN}"
            ''',
        )
        result = _run([tmp_path], allowlist=_empty_allowlist(tmp_path))
        assert result.returncode == 1, result.stdout
        assert ORPHAN in result.stdout


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


class TestAllowlist:
    def test_allowlisted_orphan_suppressed(self, tmp_path: Path) -> None:
        """An orphan present in the allowlist must not fail the gate."""
        _write_py(tmp_path, "bad.py", f'SUBJECT = "{ORPHAN}"\n')
        allow = tmp_path / "allow.txt"
        allow.write_text(f"# baselined\n{ORPHAN}\n")
        result = _run([tmp_path], allowlist=allow)
        assert result.returncode == 0, result.stdout

    def test_allowlist_comments_ignored(self, tmp_path: Path) -> None:
        """Inline comments and blank lines in the allowlist are ignored."""
        _write_py(tmp_path, "bad.py", f'SUBJECT = "{ORPHAN}"\n')
        allow = tmp_path / "allow.txt"
        allow.write_text(f"\n  {ORPHAN}  # keep until declared\n\n")
        result = _run([tmp_path], allowlist=allow)
        assert result.returncode == 0, result.stdout


# ---------------------------------------------------------------------------
# Exclusion + scanner-failure
# ---------------------------------------------------------------------------


class TestExclusion:
    def test_test_files_excluded(self, tmp_path: Path) -> None:
        _write_py(tmp_path, "test_thing.py", f'SUBJECT = "{ORPHAN}"\n')
        result = _run([tmp_path], allowlist=_empty_allowlist(tmp_path))
        assert result.returncode == 0
        assert "FAIL" not in result.stdout

    def test_tests_subdir_excluded(self, tmp_path: Path) -> None:
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        _write_py(tests_dir, "helper.py", f'SUBJECT = "{ORPHAN}"\n')
        result = _run([tmp_path], allowlist=_empty_allowlist(tmp_path))
        assert result.returncode == 0

    def test_conftest_excluded(self, tmp_path: Path) -> None:
        """conftest.py is excluded from scanning — orphan inside it is not flagged."""
        _write_py(tmp_path, "conftest.py", f'SUBJECT = "{ORPHAN}"\n')
        result = _run([tmp_path], allowlist=_empty_allowlist(tmp_path))
        assert result.returncode == 0
        assert "FAIL" not in result.stdout


class TestSyntaxError:
    def test_unparseable_file_exits_2(self, tmp_path: Path) -> None:
        """A .py file with invalid syntax causes the scanner to exit 2."""
        broken = tmp_path / "broken.py"
        broken.write_text("def broken(:\n    pass\n")
        result = _run(
            [tmp_path],
            allowlist=_empty_allowlist(tmp_path),
            root=tmp_path,
        )
        assert result.returncode == 2, result.stdout + result.stderr
        assert (
            "ERROR" in result.stderr
            or "syntax" in result.stderr.lower()
            or "parse" in result.stderr.lower()
        )


class TestMissingSrcTarget:
    def test_nonexistent_src_exits_1(self, tmp_path: Path) -> None:
        result = _run([tmp_path / "does_not_exist"])
        assert result.returncode == 1

    def test_nonexistent_src_error_on_stderr(self, tmp_path: Path) -> None:
        result = _run([tmp_path / "does_not_exist"])
        assert "ERROR" in result.stderr or "not found" in result.stderr
