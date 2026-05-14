"""Tests for tools/classify_quality_debt.py.

Contract:
  CLI: python tools/classify_quality_debt.py [--report PATH] [--dry-run|--apply]
  Default report: artifacts/quality-debt-report.json (resolved from cwd)

Dry-run stdout format (TSV per UNTAGGED entry):
  <path>\\t<line>\\t<rule>\\t<suggestion>\\t<fix_class>

Summary line on stdout:
  CLASSIFIED: <n> / UNTAGGED: <m> / NEEDS_REVIEW: <k> / RATIO: <r>
  RATIO = classified / (m - k), skipped when (m - k) == 0.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from tools.classify_quality_debt import _SLUG_RE, _parse_frontmatter, _safe_repo_path

REPO = Path(__file__).resolve().parent.parent.parent
TOOL = REPO / "tools" / "classify_quality_debt.py"

_INDEX_HEADER = (
    "# Quality-Debt Registry — INDEX\n\n"
    "| Slug | Status | Rules | Sites | Drain slice | Created |\n"
    "|------|--------|-------|-------|-------------|------|\n"
    "<!-- rows inserted here -->\n"
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _row(path: str, rule: str, line: int = 1) -> dict[str, Any]:
    return {
        "source": "noqa",
        "path": path,
        "bucket": "UNTAGGED",
        "rule": rule,
        "line": line,
    }


def _write_report(p: Path, rows: list[dict[str, Any]]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "generated_at": "2026-05-11T16:00:00Z",
                "sources": ["noqa"],
                "rows": rows,
                "stale_references": [],
                "counts_by_rule_bucket_slug": {},
            }
        )
    )


def _src(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _run(
    root: Path, report: Path, mode: str = "--dry-run"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), mode, "--report", str(report)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(root),
    )


def _tsv(stdout: str, skip_header: bool = True) -> list[dict[str, str]]:
    """Parse TSV lines: path\\tline\\trule\\tsuggestion\\tfix_class.

    skip_header=True (default) filters the header row so callers only
    see real data rows.
    """
    out = []
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 5:
            if skip_header and parts[0] == "path":
                continue
            out.append(
                {
                    "path": parts[0],
                    "line": parts[1],
                    "rule": parts[2],
                    "suggestion": parts[3],
                    "fix_class": parts[4],
                }
            )
    return out


def _debt_dir(root: Path) -> None:
    d = root / "artifacts" / "debt"
    d.mkdir(parents=True, exist_ok=True)
    (d / "INDEX.md").write_text(_INDEX_HEADER)


# ---------------------------------------------------------------------------
# test cases
# ---------------------------------------------------------------------------


def test_ble001_cli_toplevel_suggests_boundary_broad_catch(
    tmp_path: Path,
) -> None:
    """BLE001 in cli_*.py -> DEBT:boundary-broad-catch, fix_class=easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/cli/cli_main.py"
    _src(tmp_path, sp, "def handle():  # noqa: BLE001\n    pass\n")
    _write_report(rpt, [_row(sp, "BLE001")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    rows = _tsv(cp.stdout)
    assert rows, f"no TSV rows; stdout:\n{cp.stdout}"
    assert any(
        r["rule"] == "BLE001" and r["suggestion"] == "DEBT:boundary-broad-catch"
        for r in rows
    ), f"got rows={rows}"


def test_b008_typer_option_suggests_typer_default_option(tmp_path: Path) -> None:
    """B008 on line with typer.Option( -> DEBT:typer-default-option, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/cli/args.py"
    _src(
        tmp_path,
        sp,
        "import typer\n\ndef cmd(x: int = typer.Option(0)):  # noqa: B008\n    pass\n",
    )
    _write_report(rpt, [_row(sp, "B008", line=3)])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "B008"
        and r["suggestion"] == "DEBT:typer-default-option"
        and r["fix_class"] == "easy"
        for r in rows
    ), f"got rows={rows}"


def test_plr0913_wiring_path_suggests_wiring_bootstrap_deps(
    tmp_path: Path,
) -> None:
    """PLR0913 in bootstrap/* -> DEBT:wiring-bootstrap-deps, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/bootstrap/wire.py"
    _src(tmp_path, sp, "def build(a, b, c, d, e, f, g):  # noqa: PLR0913\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0913")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "PLR0913"
        and r["suggestion"] == "DEBT:wiring-bootstrap-deps"
        and r["fix_class"] == "easy"
        for r in rows
    ), f"got rows={rows}"


def test_c901_migration_function_suggests_migration_sequence(
    tmp_path: Path,
) -> None:
    """C901 on def _atomic_*(...): -> DEBT:migration-sequence-bootstrap, medium."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/db/migrations.py"
    _src(
        tmp_path,
        sp,
        "def _atomic_table_copy(src, dst, conn):  # noqa: C901\n    pass\n",
    )
    _write_report(rpt, [_row(sp, "C901")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "C901"
        and r["suggestion"] == "DEBT:migration-sequence-bootstrap"
        and r["fix_class"] == "medium"
        for r in rows
    ), f"got rows={rows}"


def test_f401_in_init_is_reexport_legacy(tmp_path: Path) -> None:
    """F401 in __init__.py -> DEBT:re-export-init (Rule 5)."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/x/__init__.py"
    _src(tmp_path, sp, "from lyra.x.impl import Foo  # noqa: F401\n")
    _write_report(rpt, [_row(sp, "F401")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "F401"
        and r["suggestion"] == "DEBT:re-export-init"
        and r["fix_class"] == "easy"
        for r in rows
    ), f"got rows={rows}"


def test_classification_ratio_above_80pct(tmp_path: Path) -> None:
    """8 classifiable + 2 needs_review -> RATIO >= 0.80 in stdout summary."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    rows: list[dict[str, Any]] = []
    for i in range(2):
        p = f"src/lyra/cli/cli_cmd{i}.py"
        _src(tmp_path, p, "def h():  # noqa: BLE001\n    pass\n")
        rows.append(_row(p, "BLE001"))
    for i in range(2):
        p = f"src/lyra/cli/opt{i}.py"
        _src(tmp_path, p, "def cmd(x=typer.Option(0)):  # noqa: B008\n    pass\n")
        rows.append(_row(p, "B008"))
    for i in range(2):
        p = f"src/lyra/bootstrap/wire{i}.py"
        _src(tmp_path, p, "def w(a,b,c,d,e,f,g):  # noqa: PLR0913\n    pass\n")
        rows.append(_row(p, "PLR0913"))
    for i in range(2):
        p = f"src/lyra/db/mig{i}.py"
        _src(tmp_path, p, f"def _atomic_step{i}(x):  # noqa: C901\n    pass\n")
        rows.append(_row(p, "C901"))
    for i in range(2):
        p = f"src/lyra/mod{i}/__init__.py"
        _src(tmp_path, p, "from lyra.x import Foo  # noqa: F401\n")
        rows.append(_row(p, "F401"))
    _write_report(rpt, rows)

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    ratio_line = next(
        (ln for ln in cp.stdout.splitlines() if "RATIO" in ln.upper()), None
    )
    assert ratio_line is not None, f"no RATIO line; stdout:\n{cp.stdout}"
    m = re.search(r"RATIO[:\s]+([0-9.]+)", ratio_line, re.IGNORECASE)
    assert m is not None, f"could not parse ratio from: {ratio_line}"
    assert float(m.group(1)) >= 0.80, f"ratio too low: {ratio_line}"


def test_apply_writes_inline_debt_suffix(tmp_path: Path) -> None:
    """--apply writes the inline DEBT:<slug> suffix on UNTAGGED rows."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    _debt_dir(tmp_path)
    sp = "src/lyra/cli/cli_main.py"
    _src(tmp_path, sp, "def h():  # noqa: BLE001\n    pass\n")
    _write_report(rpt, [_row(sp, "BLE001")])

    cp = _run(tmp_path, rpt, "--apply")

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    edited = (tmp_path / sp).read_text()
    assert "DEBT:boundary-broad-catch" in edited, (
        f"expected DEBT:boundary-broad-catch in source; got:\n{edited}"
    )


def test_apply_is_idempotent(tmp_path: Path) -> None:
    """Running --apply twice does not double-suffix annotations.

    _SUFFIX_ALREADY_RE guards against double DEBT: suffixes on re-runs.
    Regression guard for the write-mode safety contract.
    """
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    _debt_dir(tmp_path)
    sp = "src/lyra/cli/cli_main.py"
    _src(tmp_path, sp, "def h():  # noqa: BLE001\n    pass\n")
    _write_report(rpt, [_row(sp, "BLE001")])

    cp1 = _run(tmp_path, rpt, "--apply")
    assert cp1.returncode == 0, f"first run rc={cp1.returncode}\n{cp1.stderr}"
    after_first = (tmp_path / sp).read_text()

    cp2 = _run(tmp_path, rpt, "--apply")
    assert cp2.returncode == 0, f"second run rc={cp2.returncode}\n{cp2.stderr}"
    after_second = (tmp_path / sp).read_text()

    assert after_first == after_second, (
        f"second --apply mutated already-tagged row\nbefore:\n{after_first}\n"
        f"after:\n{after_second}"
    )
    # Belt-and-suspenders: count occurrences explicitly
    count = after_second.count("DEBT:boundary-broad-catch")
    assert count == 1, (
        f"expected exactly 1 DEBT:boundary-broad-catch occurrence; "
        f"got {count} in:\n{after_second}"
    )


def test_apply_creates_registry_for_debt_suggestion(tmp_path: Path) -> None:
    """--apply creates artifacts/debt/<slug>.md on DEBT:<slug> suggestion."""
    import pytest

    pytest.skip(
        "Current classifier heuristics emit POLICY only; DEBT-emitting patterns "
        "tracked in P2a #1163."
    )


def test_apply_produces_drain_queue_with_cap(tmp_path: Path) -> None:
    """--apply with 55 easy rows -> drain queue exists with <=30 easy entries."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    _debt_dir(tmp_path)
    rows: list[dict[str, Any]] = []
    for i in range(55):
        p = f"src/lyra/bootstrap/wire{i}.py"
        _src(tmp_path, p, "def w(a,b,c,d,e,f,g):  # noqa: PLR0913\n    pass\n")
        rows.append(_row(p, "PLR0913"))
    _write_report(rpt, rows)

    cp = _run(tmp_path, rpt, "--apply")

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    queue_path = tmp_path / "artifacts" / "quality-debt-drain-queue.json"
    assert queue_path.exists(), (
        f"drain queue missing; stdout:\n{cp.stdout}\nstderr:\n{cp.stderr}"
    )
    queue = json.loads(queue_path.read_text())
    assert isinstance(queue, list) and len(queue) > 0, "drain queue empty"
    easy = [e for e in queue if e.get("fix_class") == "easy"]
    assert len(easy) <= 30, f"easy cap exceeded: {len(easy)} > 30"


# ---------------------------------------------------------------------------
# Rule 1: BLE001 — broadened boundary detection
# ---------------------------------------------------------------------------


def test_ble001_adapter_path_is_boundary_broad_catch(tmp_path: Path) -> None:
    """BLE001 in src/lyra/adapters/* -> DEBT:boundary-broad-catch, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/adapters/telegram/handler.py"
    _src(tmp_path, sp, "def run():  # noqa: BLE001\n    pass\n")
    _write_report(rpt, [_row(sp, "BLE001")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "BLE001"
        and r["suggestion"] == "DEBT:boundary-broad-catch"
        and r["fix_class"] == "easy"
        for r in rows
    ), f"got rows={rows}"


def test_ble001_bootstrap_path_is_boundary_broad_catch(tmp_path: Path) -> None:
    """BLE001 in src/lyra/bootstrap/* -> DEBT:boundary-broad-catch, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/bootstrap/startup.py"
    _src(tmp_path, sp, "def start():  # noqa: BLE001\n    pass\n")
    _write_report(rpt, [_row(sp, "BLE001")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "BLE001" and r["suggestion"] == "DEBT:boundary-broad-catch"
        for r in rows
    ), f"got rows={rows}"


def test_ble001_hub_listener_basename_is_boundary_broad_catch(
    tmp_path: Path,
) -> None:
    """BLE001 in hub_events.py -> DEBT:boundary-broad-catch (hub_* match)."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/hub_events.py"
    _src(tmp_path, sp, "def listen():  # noqa: BLE001\n    pass\n")
    _write_report(rpt, [_row(sp, "BLE001")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "BLE001" and r["suggestion"] == "DEBT:boundary-broad-catch"
        for r in rows
    ), f"got rows={rows}"


def test_ble001_nats_listener_is_boundary_broad_catch(tmp_path: Path) -> None:
    """BLE001 in nats_outbound_listener.py -> DEBT:boundary-broad-catch."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/adapters/nats/nats_outbound_listener.py"
    _src(tmp_path, sp, "def consume():  # noqa: BLE001\n    pass\n")
    _write_report(rpt, [_row(sp, "BLE001")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "BLE001" and r["suggestion"] == "DEBT:boundary-broad-catch"
        for r in rows
    ), f"got rows={rows}"


def test_ble001_fallback_is_boundary_broad_catch(tmp_path: Path) -> None:
    """BLE001 anywhere -> DEBT:boundary-broad-catch (rule-only fallback)."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/utils.py"
    _src(tmp_path, sp, "def helper():  # noqa: BLE001\n    pass\n")
    _write_report(rpt, [_row(sp, "BLE001")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "BLE001" and r["suggestion"] == "DEBT:boundary-broad-catch"
        for r in rows
    ), f"expected boundary-broad-catch; got rows={rows}"


# ---------------------------------------------------------------------------
# Rule 2: PLR0913 — broadened wiring detection
# ---------------------------------------------------------------------------


def test_plr0913_dispatcher_basename_is_wiring_bootstrap_deps(
    tmp_path: Path,
) -> None:
    """PLR0913 in *_dispatch.py -> DEBT:wiring-bootstrap-deps, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/message_dispatch.py"
    _src(tmp_path, sp, "def route(a,b,c,d,e,f,g):  # noqa: PLR0913\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0913")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "PLR0913"
        and r["suggestion"] == "DEBT:wiring-bootstrap-deps"
        and r["fix_class"] == "easy"
        for r in rows
    ), f"got rows={rows}"


def test_plr0913_builder_basename_is_wiring_bootstrap_deps(
    tmp_path: Path,
) -> None:
    """PLR0913 in agent_builder.py -> DEBT:wiring-bootstrap-deps."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/agents/agent_builder.py"
    _src(tmp_path, sp, "def build(a,b,c,d,e,f,g):  # noqa: PLR0913\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0913")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "PLR0913" and r["suggestion"] == "DEBT:wiring-bootstrap-deps"
        for r in rows
    ), f"got rows={rows}"


def test_plr0913_authenticator_basename_is_wiring_bootstrap_deps(
    tmp_path: Path,
) -> None:
    """PLR0913 in authenticator.py -> DEBT:wiring-bootstrap-deps."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/authenticator.py"
    _src(tmp_path, sp, "def auth(a,b,c,d,e,f,g):  # noqa: PLR0913\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0913")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "PLR0913" and r["suggestion"] == "DEBT:wiring-bootstrap-deps"
        for r in rows
    ), f"got rows={rows}"


def test_plr0913_fallback_is_wiring_bootstrap_deps(tmp_path: Path) -> None:
    """PLR0913 anywhere -> DEBT:wiring-bootstrap-deps (rule-only fallback)."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/processor.py"
    _src(tmp_path, sp, "def do(a,b,c,d,e,f,g):  # noqa: PLR0913\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0913")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "PLR0913" and r["suggestion"] == "DEBT:wiring-bootstrap-deps"
        for r in rows
    ), f"expected wiring-bootstrap-deps; got rows={rows}"


# ---------------------------------------------------------------------------
# Rule 3: C901 — bootstrap entry + dispatcher fallback
# ---------------------------------------------------------------------------


def test_c901_bootstrap_main_is_migration_sequence_bootstrap(
    tmp_path: Path,
) -> None:
    """C901 on def main() in bootstrap/** -> DEBT:migration-sequence-bootstrap."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/bootstrap/entry.py"
    _src(tmp_path, sp, "def main():  # noqa: C901\n    pass\n")
    _write_report(rpt, [_row(sp, "C901")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "C901" and r["suggestion"] == "DEBT:migration-sequence-bootstrap"
        for r in rows
    ), f"got rows={rows}"


def test_c901_bootstrap_standalone_is_migration_sequence_bootstrap(
    tmp_path: Path,
) -> None:
    """C901 on def hub_standalone() in bootstrap/** -> migration-sequence-bootstrap."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/bootstrap/hub.py"
    _src(tmp_path, sp, "def hub_standalone():  # noqa: C901\n    pass\n")
    _write_report(rpt, [_row(sp, "C901")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "C901" and r["suggestion"] == "DEBT:migration-sequence-bootstrap"
        for r in rows
    ), f"got rows={rows}"


def test_c901_dispatcher_basename_is_wiring_bootstrap_deps(tmp_path: Path) -> None:
    """C901 in message_pipeline.py -> DEBT:wiring-bootstrap-deps (dispatcher)."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/message_pipeline.py"
    _src(tmp_path, sp, "def process():  # noqa: C901\n    pass\n")
    _write_report(rpt, [_row(sp, "C901")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "C901" and r["suggestion"] == "DEBT:wiring-bootstrap-deps"
        for r in rows
    ), f"got rows={rows}"


def test_c901_unrelated_path_falls_back_to_complexity_residual(tmp_path: Path) -> None:
    """C901 in a non-bootstrap, non-dispatcher path -> DEBT:complexity-residual."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/helpers.py"
    _src(tmp_path, sp, "def complex_helper():  # noqa: C901\n    pass\n")
    _write_report(rpt, [_row(sp, "C901")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "C901" and r["suggestion"] == "DEBT:complexity-residual"
        for r in rows
    ), f"expected complexity-residual; got rows={rows}"


# ---------------------------------------------------------------------------
# Rule 4: PLR0915 — same heuristics as C901
# ---------------------------------------------------------------------------


def test_plr0915_bootstrap_main_is_migration_sequence_bootstrap(
    tmp_path: Path,
) -> None:
    """PLR0915 on def main() in bootstrap/** -> migration-sequence-bootstrap."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/bootstrap/runner.py"
    _src(tmp_path, sp, "def main():  # noqa: PLR0915\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0915")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "PLR0915"
        and r["suggestion"] == "DEBT:migration-sequence-bootstrap"
        for r in rows
    ), f"got rows={rows}"


def test_plr0915_dispatcher_path_is_wiring_bootstrap_deps(tmp_path: Path) -> None:
    """PLR0915 in event_emitter.py -> DEBT:wiring-bootstrap-deps."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/event_emitter.py"
    _src(tmp_path, sp, "def emit():  # noqa: PLR0915\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0915")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "PLR0915" and r["suggestion"] == "DEBT:wiring-bootstrap-deps"
        for r in rows
    ), f"got rows={rows}"


def test_plr0915_fallback_is_complexity_residual(
    tmp_path: Path,
) -> None:
    """PLR0915 in non-bootstrap, non-dispatcher path -> complexity-residual."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/utils.py"
    _src(tmp_path, sp, "def do_stuff():  # noqa: PLR0915\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0915")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "PLR0915" and r["suggestion"] == "DEBT:complexity-residual"
        for r in rows
    ), f"expected complexity-residual; got rows={rows}"


# ---------------------------------------------------------------------------
# Rule 5: F401 in __init__.py -> re-export-init (changed from needs_review)
# ---------------------------------------------------------------------------


def test_f401_in_init_is_reexport_init(tmp_path: Path) -> None:
    """F401 in __init__.py -> DEBT:re-export-init, easy (was needs_review)."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/x/__init__.py"
    _src(tmp_path, sp, "from lyra.x.impl import Foo  # noqa: F401\n")
    _write_report(rpt, [_row(sp, "F401")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "F401"
        and r["suggestion"] == "DEBT:re-export-init"
        and r["fix_class"] == "easy"
        for r in rows
    ), f"got rows={rows}"


def test_f401_not_in_init_is_reexport_init(tmp_path: Path) -> None:
    """F401 anywhere -> DEBT:re-export-init (rule-only classification)."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/types.py"
    _src(tmp_path, sp, "from lyra.x.impl import Foo  # noqa: F401\n")
    _write_report(rpt, [_row(sp, "F401")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "F401" and r["suggestion"] == "DEBT:re-export-init" for r in rows
    ), f"expected re-export-init; got rows={rows}"


# ---------------------------------------------------------------------------
# Rule 6: E402 -> module-level-patch-fixtures
# ---------------------------------------------------------------------------


def test_e402_is_module_level_patch_fixtures(tmp_path: Path) -> None:
    """E402 -> DEBT:module-level-patch-fixtures, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/patching.py"
    _src(tmp_path, sp, "import os  # noqa: E402\n")
    _write_report(rpt, [_row(sp, "E402")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "E402"
        and r["suggestion"] == "DEBT:module-level-patch-fixtures"
        and r["fix_class"] == "easy"
        for r in rows
    ), f"got rows={rows}"


def test_e402_no_path_filter(tmp_path: Path) -> None:
    """E402 fires regardless of path (no path filter)."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/adapters/discord/setup.py"
    _src(tmp_path, sp, "import sys  # noqa: E402\n")
    _write_report(rpt, [_row(sp, "E402")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "E402" and r["suggestion"] == "DEBT:module-level-patch-fixtures"
        for r in rows
    ), f"got rows={rows}"


# ---------------------------------------------------------------------------
# Rule 7: PLR0912 — same heuristics as C901
# ---------------------------------------------------------------------------


def test_plr0912_bootstrap_entry_is_migration_sequence_bootstrap(
    tmp_path: Path,
) -> None:
    """PLR0912 on def bootstrap_hub() in bootstrap/** -> migration-sequence."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/bootstrap/hub.py"
    _src(tmp_path, sp, "def bootstrap_hub():  # noqa: PLR0912\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0912")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "PLR0912"
        and r["suggestion"] == "DEBT:migration-sequence-bootstrap"
        for r in rows
    ), f"got rows={rows}"


def test_plr0912_dispatcher_is_wiring_bootstrap_deps(tmp_path: Path) -> None:
    """PLR0912 in outbound_processor.py -> DEBT:wiring-bootstrap-deps."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/outbound_processor.py"
    _src(tmp_path, sp, "def route():  # noqa: PLR0912\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0912")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "PLR0912" and r["suggestion"] == "DEBT:wiring-bootstrap-deps"
        for r in rows
    ), f"got rows={rows}"


# ---------------------------------------------------------------------------
# Rule 8: pyright / mypy type annotation rules
# ---------------------------------------------------------------------------


def test_report_unnecessary_isinstance_is_defensive_narrow(
    tmp_path: Path,
) -> None:
    """reportUnnecessaryIsInstance -> DEBT:defensive-narrow-payloads, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/types.py"
    _src(
        tmp_path,
        sp,
        "if isinstance(x, str):  "
        "# pyright: ignore[reportUnnecessaryIsInstance]\n"
        "    pass\n",
    )
    _write_report(rpt, [_row(sp, "reportUnnecessaryIsInstance")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "reportUnnecessaryIsInstance"
        and r["suggestion"] == "DEBT:defensive-narrow-payloads"
        and r["fix_class"] == "easy"
        for r in rows
    ), f"got rows={rows}"


def test_report_unused_class_is_protocol_private_ducktyping(
    tmp_path: Path,
) -> None:
    """reportUnusedClass -> DEBT:protocol-private-ducktyping, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/protocols.py"
    _src(
        tmp_path,
        sp,
        "class _Internal:  # pyright: ignore[reportUnusedClass]\n    pass\n",
    )
    _write_report(rpt, [_row(sp, "reportUnusedClass")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "reportUnusedClass"
        and r["suggestion"] == "DEBT:protocol-private-ducktyping"
        and r["fix_class"] == "easy"
        for r in rows
    ), f"got rows={rows}"


def test_union_attr_is_defensive_narrow_payloads(tmp_path: Path) -> None:
    """union-attr -> DEBT:defensive-narrow-payloads, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/resolver.py"
    _src(tmp_path, sp, "x.method()  # type: ignore[union-attr]\n")
    _write_report(rpt, [_row(sp, "union-attr")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "union-attr"
        and r["suggestion"] == "DEBT:defensive-narrow-payloads"
        and r["fix_class"] == "easy"
        for r in rows
    ), f"got rows={rows}"


def test_misc_type_ignore_is_defensive_narrow_payloads(tmp_path: Path) -> None:
    """misc (type: ignore[misc]) -> DEBT:defensive-narrow-payloads, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/compat.py"
    _src(tmp_path, sp, "result = fn()  # type: ignore[misc]\n")
    _write_report(rpt, [_row(sp, "misc")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "misc"
        and r["suggestion"] == "DEBT:defensive-narrow-payloads"
        and r["fix_class"] == "easy"
        for r in rows
    ), f"got rows={rows}"


# ---------------------------------------------------------------------------
# Rule 9: PLC0415 -> DEBT:plc0415-deferred-import
# ---------------------------------------------------------------------------


def test_plc0415_is_deferred_import_debt(tmp_path: Path) -> None:
    """PLC0415 -> DEBT:plc0415-deferred-import, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/lazy.py"
    _src(
        tmp_path,
        sp,
        "def get_thing():\n    import heavy  # noqa: PLC0415\n    return heavy\n",
    )
    _write_report(rpt, [_row(sp, "PLC0415", line=2)])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "PLC0415"
        and r["suggestion"] == "DEBT:plc0415-deferred-import"
        and r["fix_class"] == "easy"
        for r in rows
    ), f"got rows={rows}"


def test_plc0415_apply_creates_debt_registry(tmp_path: Path) -> None:
    """--apply with PLC0415 creates artifacts/debt/plc0415-deferred-import.md."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    _debt_dir(tmp_path)
    sp = "src/lyra/core/lazy.py"
    _src(
        tmp_path,
        sp,
        "def get_thing():\n    import heavy  # noqa: PLC0415\n    return heavy\n",
    )
    _write_report(rpt, [_row(sp, "PLC0415", line=2)])

    cp = _run(tmp_path, rpt, "--apply")

    assert cp.returncode == 0, cp.stderr
    reg = tmp_path / "artifacts" / "debt" / "plc0415-deferred-import.md"
    assert reg.exists(), f"registry file missing; stderr:\n{cp.stderr}"


# ---------------------------------------------------------------------------
# --json mode
# ---------------------------------------------------------------------------


def _run_json(root: Path, report: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), "--dry-run", "--json", "--report", str(report)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(root),
    )


def test_json_mode_emits_valid_json_array(tmp_path: Path) -> None:
    """--dry-run --json emits a JSON array, not TSV."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/cli/cli_main.py"
    _src(tmp_path, sp, "def h():  # noqa: BLE001\n    pass\n")
    _write_report(rpt, [_row(sp, "BLE001")])

    cp = _run_json(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    assert isinstance(data, list), f"expected list; got {type(data)}"
    assert len(data) == 1
    rec = data[0]
    assert rec["rule"] == "BLE001"
    assert rec["suggestion"] == "DEBT:boundary-broad-catch"
    assert rec["fix_class"] == "easy"
    assert "path" in rec and "line" in rec and "reason" in rec


def test_json_mode_no_tsv_header(tmp_path: Path) -> None:
    """--dry-run --json stdout contains no TSV 'path\\tline' header line."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/bootstrap/startup.py"
    _src(tmp_path, sp, "def start():  # noqa: BLE001\n    pass\n")
    _write_report(rpt, [_row(sp, "BLE001")])

    cp = _run_json(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    assert "path\tline" not in cp.stdout, "TSV header leaked into JSON output"
    assert "CLASSIFIED:" not in cp.stdout, "summary line leaked into JSON output"


def test_json_mode_needs_review_rows_included(tmp_path: Path) -> None:
    """--dry-run --json includes needs_review rows with empty suggestion."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/utils.py"
    # Use an unknown rule so it falls to needs_review (every known rule classifies).
    _src(tmp_path, sp, "x = 1  # noqa: UNKNOWN999\n")
    _write_report(rpt, [_row(sp, "UNKNOWN999")])

    cp = _run_json(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    assert len(data) == 1
    rec = data[0]
    assert rec["rule"] == "UNKNOWN999"
    assert rec["fix_class"] == "needs_review"


# T2b — _SLUG_RE accept/reject (classifier copy at line 74)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "slug, expected",
    [
        ("valid-slug-1", True),
        ("a", True),
        ("Foo", False),
        ("a/b", False),
        ("-leading", False),
        ("", False),
    ],
)
def test_classifier_slug_regex(slug: str, expected: bool) -> None:
    """_SLUG_RE accepts valid lowercase-alphanumeric slugs and rejects others."""
    # Arrange: slug under test, expected match result
    # Act
    match = _SLUG_RE.fullmatch(slug)
    # Assert
    assert bool(match) == expected, (
        f"_SLUG_RE.fullmatch({slug!r}) returned {match!r}; expected match={expected}"
    )


# ---------------------------------------------------------------------------
# T3 — Batched-edits dedup: single write per file for >=2 DEBT tags
# ---------------------------------------------------------------------------


def test_apply_writes_once_for_multiple_debt_tags(tmp_path: Path) -> None:
    """--apply on a file with 2 DEBT-tagged rows edits both lines in one pass.

    Verify that both lines carry the inline suffix (proving batched write
    succeeded) and that no 'skipped duplicate edit' appears in stderr
    (proving the dedup guard did not fire on distinct line numbers).
    """
    # Arrange
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    _debt_dir(tmp_path)
    sp = "src/lyra/cli/cli_cmd.py"
    # Two BLE001 violations on separate lines -> DEBT:boundary-broad-catch
    _src(
        tmp_path,
        sp,
        "def handle_a():  # noqa: BLE001\n"
        "    pass\n"
        "\n"
        "def handle_b():  # noqa: BLE001\n"
        "    pass\n",
    )
    _write_report(
        rpt,
        [_row(sp, "BLE001", line=1), _row(sp, "BLE001", line=4)],
    )

    # Act
    cp = _run(tmp_path, rpt, "--apply")

    # Assert: process succeeded
    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"

    edited = (tmp_path / sp).read_text()

    # Both lines must carry the suffix — proves batched write reached all edits
    lines = edited.splitlines()
    assert "DEBT:boundary-broad-catch" in lines[0], (
        f"line 1 missing DEBT:boundary-broad-catch; got: {lines[0]!r}"
    )
    assert "DEBT:boundary-broad-catch" in lines[3], (
        f"line 4 missing DEBT:boundary-broad-catch; got: {lines[3]!r}"
    )

    # No duplicate-edit warning — distinct line numbers must not trigger dedup guard
    assert "skipped duplicate edit" not in cp.stderr, (
        f"unexpected dedup warning in stderr:\n{cp.stderr}"
    )


def test_apply_dedup_guard_fires_on_duplicate_line(tmp_path: Path) -> None:
    """--apply with two report rows pointing at the same line deduplicates writes.

    The _apply_file_edits guard (tools/classify_quality_debt.py, 'if lineno in seen')
    must:
      - emit "skipped duplicate edit" in stderr for the second row
      - write exactly ONE DEBT: suffix on that line (not two, not a corrupted line)
    """
    # Arrange
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    _debt_dir(tmp_path)
    sp = "src/lyra/cli/cli_main.py"
    # Source file: single BLE001 noqa on line 1
    _src(tmp_path, sp, "def h():  # noqa: BLE001\n    pass\n")
    # Report: TWO rows both pointing at line 1 of the same file
    _write_report(
        rpt,
        [_row(sp, "BLE001", line=1), _row(sp, "BLE001", line=1)],
    )

    # Act
    cp = _run(tmp_path, rpt, "--apply")

    # Assert: process succeeded
    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"

    # Assert: dedup guard fired for the second duplicate
    assert "skipped duplicate edit" in cp.stderr, (
        f"expected 'skipped duplicate edit' in stderr; got:\n{cp.stderr}"
    )

    # Assert: exactly ONE DEBT: suffix on the line (not two, not corrupted)
    edited = (tmp_path / sp).read_text()
    first_line = edited.splitlines()[0]
    count = first_line.count("DEBT:boundary-broad-catch")
    assert count == 1, (
        f"expected exactly one 'DEBT:boundary-broad-catch' on line 1; "
        f"got: {first_line!r}"
    )


# ---------------------------------------------------------------------------
# T8 — reason field propagation through drain-queue JSON output
# ---------------------------------------------------------------------------


def test_drain_queue_carries_reason_field(tmp_path: Path) -> None:
    """drain-queue entries carry a 'reason' field set by _classify_row.

    PLR0913 on a wiring path -> status=classified, reason='matched'.
    The drain-queue JSON entry for that row must have reason='matched'.
    """
    # Arrange
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    _debt_dir(tmp_path)
    sp = "src/lyra/bootstrap/wire.py"
    _src(tmp_path, sp, "def build(a, b, c, d, e, f, g):  # noqa: PLR0913\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0913")])

    # Act
    cp = _run(tmp_path, rpt, "--apply")

    # Assert: process succeeded and queue exists
    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    queue_path = tmp_path / "artifacts" / "quality-debt-drain-queue.json"
    assert queue_path.exists(), (
        f"drain queue missing; stdout:\n{cp.stdout}\nstderr:\n{cp.stderr}"
    )

    queue = json.loads(queue_path.read_text())
    assert isinstance(queue, list) and len(queue) > 0, "drain queue is empty"

    entry = queue[0]
    # 'reason' key must be present and non-empty
    assert "reason" in entry, f"'reason' key missing from drain-queue entry: {entry}"
    assert entry["reason"], f"'reason' is empty in drain-queue entry: {entry}"
    # PLR0913 on wiring path -> matched heuristic
    assert entry["reason"] == "matched", (
        f"expected reason='matched', got {entry['reason']!r}"
    )


# ---------------------------------------------------------------------------
# T9 — _safe_repo_path containment + apply-path no-write
# ---------------------------------------------------------------------------


def test_safe_repo_path_blocks_traversal(tmp_path: Path) -> None:
    """_safe_repo_path returns None for paths that escape the repo root."""
    # Arrange: tmp_path is the root

    # Act + Assert: path traversal attempts return None
    assert _safe_repo_path(tmp_path, "../escape") is None, (
        "_safe_repo_path should return None for '../escape'"
    )
    assert _safe_repo_path(tmp_path, "/abs/outside") is None, (
        "_safe_repo_path should return None for '/abs/outside'"
    )

    # Act + Assert: valid relative path inside root returns a Path
    result = _safe_repo_path(tmp_path, "valid/sub.py")
    assert result is not None, "_safe_repo_path should return a Path for 'valid/sub.py'"
    assert result.is_relative_to(tmp_path), (
        f"returned path {result} is not inside root {tmp_path}"
    )


def test_apply_does_not_write_outside_root(tmp_path: Path) -> None:
    """--apply with a traversal path in the report skips that row silently.

    The guard at lines 400 and 434 of classify_quality_debt.py must
    short-circuit without writing any file outside tmp_path and without
    raising an unhandled exception (rc must be 0).
    """
    # Arrange: sibling directory that the traversal path would escape to
    sibling = tmp_path.parent / f"escape_target_{tmp_path.name}"
    sibling.mkdir(exist_ok=True)
    escape_file = sibling / "should_not_be_written.py"

    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    _debt_dir(tmp_path)

    # Build a path string that resolves outside tmp_path
    # e.g. "../escape_target_<name>/should_not_be_written.py"
    relative_escape = f"../escape_target_{tmp_path.name}/should_not_be_written.py"

    # Also plant a legitimate row so the apply path has something to do
    sp = "src/lyra/cli/cli_main.py"
    _src(tmp_path, sp, "def h():  # noqa: BLE001\n    pass\n")
    _write_report(
        rpt,
        [
            _row(relative_escape, "BLE001", line=1),
            _row(sp, "BLE001", line=1),
        ],
    )

    # Act
    cp = _run(tmp_path, rpt, "--apply")

    # Assert: no unhandled exception (rc=0)
    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"

    # Assert: the escape file was never created
    assert not escape_file.exists(), (
        f"apply wrote outside root: {escape_file} was created"
    )

    # Assert: the legitimate row WAS processed — proves apply continued after skip
    assert "DEBT:boundary-broad-catch" in (tmp_path / sp).read_text(), (
        f"--apply did not process the legitimate row at {sp}"
    )


# ---------------------------------------------------------------------------
# _parse_frontmatter — list-valued fields must round-trip (regression: #1179)
# ---------------------------------------------------------------------------


def test_parse_frontmatter_multi_value_rules_list() -> None:
    content = (
        "---\n"
        "slug: wiring-bootstrap-deps\n"
        "status: open\n"
        "rules:\n"
        "  - PLR0913\n"
        "  - C901\n"
        "drain_slice: async-pipeline\n"
        "---\n"
        "\nbody\n"
    )
    fm = _parse_frontmatter(content)
    assert fm["rules"] == ["PLR0913", "C901"]
    assert fm["slug"] == "wiring-bootstrap-deps"
    assert fm["drain_slice"] == "async-pipeline"


def test_parse_frontmatter_scalar_rules_preserved() -> None:
    content = "---\nslug: x\nrules: C901\n---\nbody\n"
    fm = _parse_frontmatter(content)
    assert fm["rules"] == "C901"


def test_parse_frontmatter_no_frontmatter_returns_empty() -> None:
    assert _parse_frontmatter("no frontmatter here") == {}


def test_update_index_renders_full_rules_list_for_multi_rule_slug(
    tmp_path: Path,
) -> None:
    """_update_index must render every entry of a multi-rule `rules:` list,
    not just the first (the bug fixed in #1179)."""
    debt_dir = tmp_path / "artifacts" / "debt"
    debt_dir.mkdir(parents=True)
    (debt_dir / "multi-rule-stub.md").write_text(
        "---\n"
        "slug: multi-rule-stub\n"
        "status: open\n"
        "rules:\n"
        "  - PLR0913\n"
        "  - C901\n"
        "drain_slice: async-pipeline\n"
        "created: 2026-05-14\n"
        "---\n",
        encoding="utf-8",
    )

    from tools.classify_quality_debt import _update_index

    _update_index(debt_dir)

    index_md = (debt_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "PLR0913, C901" in index_md, index_md
    assert "multi-rule-stub" in index_md
