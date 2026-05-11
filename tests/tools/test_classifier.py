"""RED tests for tools/classify_quality_debt.py.

Tool does not exist yet — all tests FAIL. Correct RED state for this phase.

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
    return {"source": "noqa", "path": path, "bucket": "UNTAGGED",
            "rule": rule, "line": line}


def _write_report(p: Path, rows: list[dict[str, Any]]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "generated_at": "2026-05-11T16:00:00Z", "sources": ["noqa"],
        "rows": rows, "stale_references": [], "counts_by_rule_bucket_slug": {},
    }))


def _src(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _run(
    root: Path, report: Path, mode: str = "--dry-run"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), mode, "--report", str(report)],
        capture_output=True, text=True, check=False, cwd=str(root),
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
            out.append({"path": parts[0], "line": parts[1], "rule": parts[2],
                        "suggestion": parts[3], "fix_class": parts[4]})
    return out


def _debt_dir(root: Path) -> None:
    d = root / "artifacts" / "debt"
    d.mkdir(parents=True, exist_ok=True)
    (d / "INDEX.md").write_text(_INDEX_HEADER)


# ---------------------------------------------------------------------------
# test cases
# ---------------------------------------------------------------------------


def test_ble001_at_cli_toplevel_suggests_policy_boundary(tmp_path: Path) -> None:
    """BLE001 in cli_*.py -> POLICY:boundary, fix_class=easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/cli/cli_main.py"
    _src(tmp_path, sp, "def handle():  # noqa: BLE001\n    pass\n")
    _write_report(rpt, [_row(sp, "BLE001")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    rows = _tsv(cp.stdout)
    assert rows, f"no TSV rows; stdout:\n{cp.stdout}"
    assert any(r["rule"] == "BLE001" and r["suggestion"] == "POLICY:boundary"
               for r in rows), f"got rows={rows}"


def test_b008_typer_option_suggests_policy_typer_default(tmp_path: Path) -> None:
    """B008 on line with typer.Option( -> POLICY:typer-default, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/cli/args.py"
    _src(tmp_path, sp,
         "import typer\n\ndef cmd(x: int = typer.Option(0)):  # noqa: B008\n    pass\n")
    _write_report(rpt, [_row(sp, "B008", line=3)])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "B008" and r["suggestion"] == "POLICY:typer-default"
               and r["fix_class"] == "easy" for r in rows), f"got rows={rows}"


def test_plr0913_wiring_path_suggests_policy_wiring(tmp_path: Path) -> None:
    """PLR0913 in bootstrap/* -> POLICY:wiring, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/bootstrap/wire.py"
    _src(tmp_path, sp, "def build(a, b, c, d, e, f, g):  # noqa: PLR0913\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0913")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "PLR0913" and r["suggestion"] == "POLICY:wiring"
               and r["fix_class"] == "easy" for r in rows), f"got rows={rows}"


def test_c901_migration_function_suggests_policy_migration_sequence(
    tmp_path: Path,
) -> None:
    """C901 on def _atomic_*(...): -> POLICY:migration-sequence, medium."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/db/migrations.py"
    _src(tmp_path, sp,
         "def _atomic_table_copy(src, dst, conn):  # noqa: C901\n    pass\n")
    _write_report(rpt, [_row(sp, "C901")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "C901"
               and r["suggestion"] == "POLICY:migration-sequence"
               and r["fix_class"] == "medium" for r in rows), f"got rows={rows}"


def test_f401_in_init_is_reexport_legacy(tmp_path: Path) -> None:
    """F401 in __init__.py -> POLICY:re-export (Rule 5, was needs_review before)."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/x/__init__.py"
    _src(tmp_path, sp, "from lyra.x.impl import Foo  # noqa: F401\n")
    _write_report(rpt, [_row(sp, "F401")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "F401" and r["suggestion"] == "POLICY:re-export"
               and r["fix_class"] == "easy" for r in rows), f"got rows={rows}"


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


def test_apply_writes_inline_policy_suffix(tmp_path: Path) -> None:
    """--apply writes the inline POLICY:<tag> suffix on UNTAGGED rows."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    _debt_dir(tmp_path)
    sp = "src/lyra/cli/cli_main.py"
    _src(tmp_path, sp, "def h():  # noqa: BLE001\n    pass\n")
    _write_report(rpt, [_row(sp, "BLE001")])

    cp = _run(tmp_path, rpt, "--apply")

    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    edited = (tmp_path / sp).read_text()
    assert "POLICY:boundary" in edited, (
        f"expected POLICY:boundary in source; got:\n{edited}"
    )


def test_apply_is_idempotent(tmp_path: Path) -> None:
    """Running --apply twice does not double-suffix annotations.

    _SUFFIX_ALREADY_RE guards against `# noqa: BLE001 — POLICY:boundary
    — POLICY:boundary` corruption on re-runs. Regression guard for the
    write-mode safety contract.
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
    assert after_second.count("POLICY:boundary") == 1, (
        f"expected exactly 1 POLICY:boundary occurrence; got "
        f"{after_second.count('POLICY:boundary')} in:\n{after_second}"
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


def test_ble001_adapter_path_is_boundary(tmp_path: Path) -> None:
    """BLE001 in src/lyra/adapters/* -> POLICY:boundary, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/adapters/telegram/handler.py"
    _src(tmp_path, sp, "def run():  # noqa: BLE001\n    pass\n")
    _write_report(rpt, [_row(sp, "BLE001")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "BLE001" and r["suggestion"] == "POLICY:boundary"
               and r["fix_class"] == "easy" for r in rows), f"got rows={rows}"


def test_ble001_bootstrap_path_is_boundary(tmp_path: Path) -> None:
    """BLE001 in src/lyra/bootstrap/* -> POLICY:boundary, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/bootstrap/startup.py"
    _src(tmp_path, sp, "def start():  # noqa: BLE001\n    pass\n")
    _write_report(rpt, [_row(sp, "BLE001")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "BLE001" and r["suggestion"] == "POLICY:boundary"
               for r in rows), f"got rows={rows}"


def test_ble001_hub_listener_basename_is_boundary(tmp_path: Path) -> None:
    """BLE001 in hub_events.py -> POLICY:boundary (hub_* basename match)."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/hub_events.py"
    _src(tmp_path, sp, "def listen():  # noqa: BLE001\n    pass\n")
    _write_report(rpt, [_row(sp, "BLE001")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "BLE001" and r["suggestion"] == "POLICY:boundary"
               for r in rows), f"got rows={rows}"


def test_ble001_nats_listener_basename_is_boundary(tmp_path: Path) -> None:
    """BLE001 in nats_outbound_listener.py -> POLICY:boundary (*_listener match)."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/adapters/nats/nats_outbound_listener.py"
    _src(tmp_path, sp, "def consume():  # noqa: BLE001\n    pass\n")
    _write_report(rpt, [_row(sp, "BLE001")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "BLE001" and r["suggestion"] == "POLICY:boundary"
               for r in rows), f"got rows={rows}"


def test_ble001_unrelated_path_falls_back_to_boundary(tmp_path: Path) -> None:
    """BLE001 anywhere -> POLICY:boundary (rule-only fallback after path heuristics)."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/utils.py"
    _src(tmp_path, sp, "def helper():  # noqa: BLE001\n    pass\n")
    _write_report(rpt, [_row(sp, "BLE001")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "BLE001" and r["suggestion"] == "POLICY:boundary"
               for r in rows), f"expected boundary; got rows={rows}"


# ---------------------------------------------------------------------------
# Rule 2: PLR0913 — broadened wiring detection
# ---------------------------------------------------------------------------


def test_plr0913_dispatcher_basename_is_wiring(tmp_path: Path) -> None:
    """PLR0913 in *_dispatch.py -> POLICY:wiring, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/message_dispatch.py"
    _src(tmp_path, sp, "def route(a,b,c,d,e,f,g):  # noqa: PLR0913\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0913")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "PLR0913" and r["suggestion"] == "POLICY:wiring"
               and r["fix_class"] == "easy" for r in rows), f"got rows={rows}"


def test_plr0913_builder_basename_is_wiring(tmp_path: Path) -> None:
    """PLR0913 in agent_builder.py -> POLICY:wiring."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/agents/agent_builder.py"
    _src(tmp_path, sp, "def build(a,b,c,d,e,f,g):  # noqa: PLR0913\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0913")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "PLR0913" and r["suggestion"] == "POLICY:wiring"
               for r in rows), f"got rows={rows}"


def test_plr0913_authenticator_basename_is_wiring(tmp_path: Path) -> None:
    """PLR0913 in authenticator.py -> POLICY:wiring."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/authenticator.py"
    _src(tmp_path, sp, "def auth(a,b,c,d,e,f,g):  # noqa: PLR0913\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0913")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "PLR0913" and r["suggestion"] == "POLICY:wiring"
               for r in rows), f"got rows={rows}"


def test_plr0913_unrelated_basename_falls_back_to_wiring(tmp_path: Path) -> None:
    """PLR0913 anywhere -> POLICY:wiring (rule-only fallback after path heuristics)."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/processor.py"
    _src(tmp_path, sp, "def do(a,b,c,d,e,f,g):  # noqa: PLR0913\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0913")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "PLR0913" and r["suggestion"] == "POLICY:wiring"
               for r in rows), f"expected wiring; got rows={rows}"


# ---------------------------------------------------------------------------
# Rule 3: C901 — bootstrap entry + dispatcher fallback
# ---------------------------------------------------------------------------


def test_c901_bootstrap_main_is_migration_sequence(tmp_path: Path) -> None:
    """C901 on def main() in bootstrap/** -> POLICY:migration-sequence."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/bootstrap/entry.py"
    _src(tmp_path, sp, "def main():  # noqa: C901\n    pass\n")
    _write_report(rpt, [_row(sp, "C901")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "C901" and r["suggestion"] == "POLICY:migration-sequence"
               for r in rows), f"got rows={rows}"


def test_c901_bootstrap_standalone_is_migration_sequence(tmp_path: Path) -> None:
    """C901 on def hub_standalone() in bootstrap/** -> POLICY:migration-sequence."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/bootstrap/hub.py"
    _src(tmp_path, sp, "def hub_standalone():  # noqa: C901\n    pass\n")
    _write_report(rpt, [_row(sp, "C901")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "C901" and r["suggestion"] == "POLICY:migration-sequence"
               for r in rows), f"got rows={rows}"


def test_c901_dispatcher_basename_is_wiring(tmp_path: Path) -> None:
    """C901 in message_pipeline.py -> POLICY:wiring (dispatcher basename)."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/message_pipeline.py"
    _src(tmp_path, sp, "def process():  # noqa: C901\n    pass\n")
    _write_report(rpt, [_row(sp, "C901")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "C901" and r["suggestion"] == "POLICY:wiring"
               for r in rows), f"got rows={rows}"


def test_c901_unrelated_path_falls_back_to_complexity_residual(tmp_path: Path) -> None:
    """C901 in a non-bootstrap, non-dispatcher path -> DEBT:complexity-residual."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/helpers.py"
    _src(tmp_path, sp, "def complex_helper():  # noqa: C901\n    pass\n")
    _write_report(rpt, [_row(sp, "C901")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "C901" and r["suggestion"] == "DEBT:complexity-residual"
               for r in rows), f"expected complexity-residual; got rows={rows}"


# ---------------------------------------------------------------------------
# Rule 4: PLR0915 — same heuristics as C901
# ---------------------------------------------------------------------------


def test_plr0915_bootstrap_main_is_migration_sequence(tmp_path: Path) -> None:
    """PLR0915 on def main() in bootstrap/** -> POLICY:migration-sequence."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/bootstrap/runner.py"
    _src(tmp_path, sp, "def main():  # noqa: PLR0915\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0915")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "PLR0915" and r["suggestion"] == "POLICY:migration-sequence"
               for r in rows), f"got rows={rows}"


def test_plr0915_dispatcher_path_is_wiring(tmp_path: Path) -> None:
    """PLR0915 in event_emitter.py -> POLICY:wiring."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/event_emitter.py"
    _src(tmp_path, sp, "def emit():  # noqa: PLR0915\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0915")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "PLR0915" and r["suggestion"] == "POLICY:wiring"
               for r in rows), f"got rows={rows}"


def test_plr0915_unrelated_path_falls_back_to_complexity_residual(
    tmp_path: Path,
) -> None:
    """PLR0915 in non-bootstrap, non-dispatcher path -> DEBT:complexity-residual."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/utils.py"
    _src(tmp_path, sp, "def do_stuff():  # noqa: PLR0915\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0915")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "PLR0915" and r["suggestion"] == "DEBT:complexity-residual"
               for r in rows), f"expected complexity-residual; got rows={rows}"


# ---------------------------------------------------------------------------
# Rule 5: F401 in __init__.py -> re-export (changed from needs_review)
# ---------------------------------------------------------------------------


def test_f401_in_init_is_reexport(tmp_path: Path) -> None:
    """F401 in __init__.py -> POLICY:re-export, easy (changed from needs_review)."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/x/__init__.py"
    _src(tmp_path, sp, "from lyra.x.impl import Foo  # noqa: F401\n")
    _write_report(rpt, [_row(sp, "F401")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "F401" and r["suggestion"] == "POLICY:re-export"
               and r["fix_class"] == "easy" for r in rows), f"got rows={rows}"


def test_f401_not_in_init_is_reexport(tmp_path: Path) -> None:
    """F401 anywhere -> POLICY:re-export (rule-only classification)."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/types.py"
    _src(tmp_path, sp, "from lyra.x.impl import Foo  # noqa: F401\n")
    _write_report(rpt, [_row(sp, "F401")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "F401" and r["suggestion"] == "POLICY:re-export"
               for r in rows), f"expected re-export; got rows={rows}"


# ---------------------------------------------------------------------------
# Rule 6: E402 -> module-level-patch
# ---------------------------------------------------------------------------


def test_e402_is_module_level_patch(tmp_path: Path) -> None:
    """E402 -> POLICY:module-level-patch, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/patching.py"
    _src(tmp_path, sp, "import os  # noqa: E402\n")
    _write_report(rpt, [_row(sp, "E402")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "E402" and r["suggestion"] == "POLICY:module-level-patch"
               and r["fix_class"] == "easy" for r in rows), f"got rows={rows}"


def test_e402_no_path_filter(tmp_path: Path) -> None:
    """E402 fires regardless of path (no path filter)."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/adapters/discord/setup.py"
    _src(tmp_path, sp, "import sys  # noqa: E402\n")
    _write_report(rpt, [_row(sp, "E402")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "E402" and r["suggestion"] == "POLICY:module-level-patch"
               for r in rows), f"got rows={rows}"


# ---------------------------------------------------------------------------
# Rule 7: PLR0912 — same heuristics as C901
# ---------------------------------------------------------------------------


def test_plr0912_bootstrap_entry_is_migration_sequence(tmp_path: Path) -> None:
    """PLR0912 on def bootstrap_hub() in bootstrap/** -> POLICY:migration-sequence."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/bootstrap/hub.py"
    _src(tmp_path, sp, "def bootstrap_hub():  # noqa: PLR0912\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0912")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "PLR0912" and r["suggestion"] == "POLICY:migration-sequence"
               for r in rows), f"got rows={rows}"


def test_plr0912_dispatcher_is_wiring(tmp_path: Path) -> None:
    """PLR0912 in outbound_processor.py -> POLICY:wiring."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/outbound_processor.py"
    _src(tmp_path, sp, "def route():  # noqa: PLR0912\n    pass\n")
    _write_report(rpt, [_row(sp, "PLR0912")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "PLR0912" and r["suggestion"] == "POLICY:wiring"
               for r in rows), f"got rows={rows}"


# ---------------------------------------------------------------------------
# Rule 8: pyright / mypy type annotation rules
# ---------------------------------------------------------------------------


def test_report_unnecessary_isinstance_is_defensive_narrow(tmp_path: Path) -> None:
    """reportUnnecessaryIsInstance -> POLICY:defensive-narrow, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/types.py"
    _src(tmp_path, sp,
         "if isinstance(x, str):  # pyright: ignore[reportUnnecessaryIsInstance]\n"
         "    pass\n")
    _write_report(rpt, [_row(sp, "reportUnnecessaryIsInstance")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "reportUnnecessaryIsInstance"
               and r["suggestion"] == "POLICY:defensive-narrow"
               and r["fix_class"] == "easy" for r in rows), f"got rows={rows}"


def test_report_unused_class_is_protocol_private(tmp_path: Path) -> None:
    """reportUnusedClass -> POLICY:protocol-private, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/protocols.py"
    _src(tmp_path, sp,
         "class _Internal:  # pyright: ignore[reportUnusedClass]\n    pass\n")
    _write_report(rpt, [_row(sp, "reportUnusedClass")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "reportUnusedClass"
               and r["suggestion"] == "POLICY:protocol-private"
               and r["fix_class"] == "easy" for r in rows), f"got rows={rows}"


def test_union_attr_is_defensive_narrow(tmp_path: Path) -> None:
    """union-attr -> POLICY:defensive-narrow, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/resolver.py"
    _src(tmp_path, sp, "x.method()  # type: ignore[union-attr]\n")
    _write_report(rpt, [_row(sp, "union-attr")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(
        r["rule"] == "union-attr" and r["suggestion"] == "POLICY:defensive-narrow"
        and r["fix_class"] == "easy" for r in rows
    ), f"got rows={rows}"


def test_misc_type_ignore_is_defensive_narrow(tmp_path: Path) -> None:
    """misc (type: ignore[misc]) -> POLICY:defensive-narrow, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/compat.py"
    _src(tmp_path, sp, "result = fn()  # type: ignore[misc]\n")
    _write_report(rpt, [_row(sp, "misc")])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "misc" and r["suggestion"] == "POLICY:defensive-narrow"
               and r["fix_class"] == "easy" for r in rows), f"got rows={rows}"


# ---------------------------------------------------------------------------
# Rule 9: PLC0415 -> DEBT:plc0415-deferred-import
# ---------------------------------------------------------------------------


def test_plc0415_is_deferred_import_debt(tmp_path: Path) -> None:
    """PLC0415 -> DEBT:plc0415-deferred-import, easy."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    sp = "src/lyra/core/lazy.py"
    _src(tmp_path, sp,
         "def get_thing():\n    import heavy  # noqa: PLC0415\n    return heavy\n")
    _write_report(rpt, [_row(sp, "PLC0415", line=2)])

    cp = _run(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    rows = _tsv(cp.stdout)
    assert any(r["rule"] == "PLC0415"
               and r["suggestion"] == "DEBT:plc0415-deferred-import"
               and r["fix_class"] == "easy" for r in rows), f"got rows={rows}"


def test_plc0415_apply_creates_debt_registry(tmp_path: Path) -> None:
    """--apply with PLC0415 creates artifacts/debt/plc0415-deferred-import.md."""
    rpt = tmp_path / "artifacts" / "quality-debt-report.json"
    _debt_dir(tmp_path)
    sp = "src/lyra/core/lazy.py"
    _src(tmp_path, sp,
         "def get_thing():\n    import heavy  # noqa: PLC0415\n    return heavy\n")
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
        capture_output=True, text=True, check=False, cwd=str(root),
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
    assert rec["suggestion"] == "POLICY:boundary"
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
    # Use an unknown rule so it falls to needs_review (every known rule now classifies).
    _src(tmp_path, sp, "x = 1  # noqa: UNKNOWN999\n")
    _write_report(rpt, [_row(sp, "UNKNOWN999")])

    cp = _run_json(tmp_path, rpt)

    assert cp.returncode == 0, cp.stderr
    data = json.loads(cp.stdout)
    assert len(data) == 1
    rec = data[0]
    assert rec["rule"] == "UNKNOWN999"
    assert rec["fix_class"] == "needs_review"
