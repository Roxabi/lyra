#!/usr/bin/env python3
# ruff: noqa: E501
"""Emit plan.md snapshots for AC5 block-order replay (#1771)."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "artifacts/plans/1771-factory-dashboard-goal.md"

STAGES_ORDER = (
    "init",
    "pre_in_progress",
    "pre_done",
    "b1_in_progress",
    "b1_done",
    "b2_in_progress",
    "b2_done",
    "b3_in_progress",
    "b3_done",
)

JOURNAL_BY_STAGE: dict[str, str] = {
    "init": """### 2026-06-28 — Création du plan

- Plan consolidé après review tri-expert (MVP-minimal, epic-complet, risk-first).
- Verdict panel : SAFE WITH GUARDS.
- Décision : Docker/bun en sortie Block 1 (pas Block 3 seul).
- Block 2 isolé (SessionCatalog) pour éviter drift session.
""",
    "pre_in_progress": """### 2026-06-28 — Création du plan

- Plan consolidé après review tri-expert (MVP-minimal, epic-complet, risk-first).

### 2026-06-28 — Pre-flight slice (in_progress)

- Statut Pre-flight → `in_progress`
""",
    "pre_done": """### 2026-06-28 — Création du plan

- Plan consolidé après review tri-expert (MVP-minimal, epic-complet, risk-first).

### 2026-06-28 — Pre-flight slice

- [x] import-linter contracts + route extraction + AGENTS.md + contracts + Makefile + quadlet
- Gates : `lint-imports` 13/13, `wc -l web_server.py` = 30
""",
    "b1_in_progress": """### 2026-06-28 — Pre-flight slice

- [x] import-linter + contracts + Makefile + quadlet timeouts

### 2026-06-28 — Block 1 slice (in_progress)

- Statut Block 1 → `in_progress`
""",
    "b1_done": """### 2026-06-28 — Pre-flight slice

- [x] import-linter + contracts + Makefile + quadlet timeouts

### 2026-06-28 — Block 1 slice

- [x] Cockpit SPA, multi-chat, harness/model pickers, `stream_token`, Docker bun stage, CI vitest+biome
- Gates : `bun build/lint/typecheck`, vitest, pytest web_server + static mount
""",
    "b2_in_progress": """### 2026-06-28 — Block 1 slice

- [x] Cockpit SPA, multi-chat, harness/model pickers, `stream_token`

### 2026-06-28 — Block 2 slice (in_progress)

- Statut Block 2 → `in_progress`
""",
    "b2_done": """### 2026-06-28 — Block 1 slice

- [x] Cockpit SPA, multi-chat, harness/model pickers, `stream_token`

### 2026-06-28 — Block 2 slice

- [x] `session_catalog.list_sessions_for_agent`, hub `dashboard_rpc.py`, BFF `/api/bff/sessions*`
- [x] ACL `factory.dashboard.>` + `nats-regen-specs` + auth.conf drift green
- Gates : pytest hub RPC + BFF
""",
    "b3_in_progress": """### 2026-06-28 — Block 2 slice

- [x] SessionCatalog hub RPC + Reprendre panel + ACL regen

### 2026-06-28 — Block 3 slice (in_progress)

- Statut Block 3 → `in_progress`
""",
    "b3_done": """### 2026-06-28 — Block 2 slice

- [x] SessionCatalog hub RPC + Reprendre panel + ACL regen

### 2026-06-28 — Block 3 slice

- [x] `FACTORY_DASHBOARD_E2E=1`, Playwright dark/light snapshots, docs, secrets drift
- Gates : `make qg` + smoke (`b3-smoke.log`)

### 2026-06-28 — AC5 replay (git history restructure)

- Historique réécrit via `scripts/goal-1771-replay.sh` : commits par bloc avec plan `not_started`→`in_progress`→`done`
- Cherry-pick `b9dc8953..HEAD` postérieur au replay monolithique
""",
}


def _uncheck_all(text: str) -> str:
    return re.sub(r"- \[x\]", "- [ ]", text)


def _check_section(text: str, start: str, end: str) -> str:
    match = re.search(
        rf"{re.escape(start)}(.*?){re.escape(end)}",
        text,
        flags=re.DOTALL,
    )
    if not match:
        raise ValueError(f"section not found: {start!r} .. {end!r}")
    inner = re.sub(r"- \[ \]", "- [x]", match.group(1))
    return text[: match.start()] + start + inner + end + text[match.end() :]


def _set_global(text: str, status: str) -> str:
    return re.sub(
        r"(\| \*\*Statut global\*\* \| )`[^`]+`",
        rf"\1`{status}`",
        text,
        count=1,
    )


def _set_table_row(text: str, label: str, status: str, notes: str) -> str:
    return re.sub(
        rf"(\| {re.escape(label)} \| )`[^`]+`( \| [^\n]*)?",
        rf"\1`{status}` | {notes}",
        text,
        count=1,
    )


def _set_block_status(text: str, block_header: str, status: str) -> str:
    pattern = rf"(## {re.escape(block_header)}.*?\n\n\*\*Statut :\*\* )`[^`]+`"
    return re.sub(pattern, rf"\1`{status}`", text, count=1, flags=re.DOTALL)


def _replace_journal(text: str, body: str) -> str:
    return re.sub(
        r"(## Journal de progression\n\n>.*?\n\n)(.*?)(\n---\n\n## Référence)",
        rf"\1{body}\n\3",
        text,
        count=1,
        flags=re.DOTALL,
    )


def _base() -> str:
    text = _uncheck_all(PLAN.read_text(encoding="utf-8"))
    text = _set_global(text, "not_started")
    text = _set_table_row(text, "Pre-flight", "not_started", "—")
    text = _set_table_row(text, "Block 1 — Cockpit + Chat + Harness/Model", "not_started", "—")
    text = _set_table_row(text, "Block 2 — SessionCatalog + Reprendre", "not_started", "—")
    text = _set_table_row(text, "Block 3 — E2E + Hardening + Ship", "not_started", "—")
    text = _set_block_status(text, "PRE-FLIGHT — avant Block 1", "not_started")
    text = _set_block_status(text, "BLOCK 1 — Cockpit + Chat + Harness/Model", "not_started")
    text = _set_block_status(text, "BLOCK 2 — SessionCatalog + Reprendre", "not_started")
    text = _set_block_status(text, "BLOCK 3 — E2E + Hardening + Ship", "not_started")
    return _replace_journal(text, JOURNAL_BY_STAGE["init"])


def render(stage: str) -> str:
    text = _base()

    if stage == "init":
        return text

    if stage == "pre_in_progress":
        text = _set_global(text, "in_progress")
        text = _set_table_row(text, "Pre-flight", "in_progress", "import-linter + contracts")
        text = _set_block_status(text, "PRE-FLIGHT — avant Block 1", "in_progress")
        return _replace_journal(text, JOURNAL_BY_STAGE["pre_in_progress"])

    # pre_done and beyond
    text = _check_section(text, "## Invariants globaux (tous blocs)\n\n", "\n\n---\n\n## PRE-FLIGHT")
    text = _check_section(text, "## PRE-FLIGHT — avant Block 1\n\n", "\n\n---\n\n## BLOCK 1")
    text = _set_global(text, "in_progress")
    text = _set_table_row(text, "Pre-flight", "done", "import-linter + contracts + Makefile")
    text = _set_block_status(text, "PRE-FLIGHT — avant Block 1", "done")
    text = _replace_journal(text, JOURNAL_BY_STAGE["pre_done"])

    if stage == "pre_done":
        return text

    if stage == "b1_in_progress":
        text = _set_table_row(text, "Block 1 — Cockpit + Chat + Harness/Model", "in_progress", "SPA cockpit")
        text = _set_block_status(text, "BLOCK 1 — Cockpit + Chat + Harness/Model", "in_progress")
        return _replace_journal(text, JOURNAL_BY_STAGE["b1_in_progress"])

    # b1_done and beyond
    text = _check_section(
        text,
        "## BLOCK 1 — Cockpit + Chat + Harness/Model\n\n",
        "\n\n---\n\n## BLOCK 2",
    )
    text = _set_table_row(text, "Block 1 — Cockpit + Chat + Harness/Model", "done", "SPA + vitest + Docker/CI")
    text = _set_block_status(text, "BLOCK 1 — Cockpit + Chat + Harness/Model", "done")
    text = _replace_journal(text, JOURNAL_BY_STAGE["b1_done"])

    if stage == "b1_done":
        return text

    if stage == "b2_in_progress":
        text = _set_table_row(text, "Block 2 — SessionCatalog + Reprendre", "in_progress", "hub RPC")
        text = _set_block_status(text, "BLOCK 2 — SessionCatalog + Reprendre", "in_progress")
        return _replace_journal(text, JOURNAL_BY_STAGE["b2_in_progress"])

    # b2_done and beyond
    text = _check_section(
        text,
        "## BLOCK 2 — SessionCatalog + Reprendre\n\n",
        "\n\n---\n\n## BLOCK 3",
    )
    text = _set_table_row(text, "Block 2 — SessionCatalog + Reprendre", "done", "hub RPC + BFF + ACL regen")
    text = _set_block_status(text, "BLOCK 2 — SessionCatalog + Reprendre", "done")
    text = _replace_journal(text, JOURNAL_BY_STAGE["b2_done"])

    if stage == "b2_done":
        return text

    if stage == "b3_in_progress":
        text = _set_table_row(text, "Block 3 — E2E + Hardening + Ship", "in_progress", "E2E + docs")
        text = _set_block_status(text, "BLOCK 3 — E2E + Hardening + Ship", "in_progress")
        return _replace_journal(text, JOURNAL_BY_STAGE["b3_in_progress"])

    # b3_done
    text = _check_section(
        text,
        "## BLOCK 3 — E2E + Hardening + Ship\n\n",
        "\n\n---\n\n## Hors scope",
    )
    text = _set_global(text, "done")
    text = _set_table_row(text, "Block 3 — E2E + Hardening + Ship", "done", "Playwright visual + push staging")
    text = _set_block_status(text, "BLOCK 3 — E2E + Hardening + Ship", "done")
    return _replace_journal(text, JOURNAL_BY_STAGE["b3_done"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=STAGES_ORDER)
    args = parser.parse_args()
    sys.stdout.write(render(args.stage))


if __name__ == "__main__":
    main()