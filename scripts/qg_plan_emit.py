#!/usr/bin/env python3
"""Emit CI plan JSON for scripts/qg plan — stdin: gate lines name|action|reason."""

from __future__ import annotations

import json
import sys
from typing import Any


def parse_gates() -> list[dict[str, str]]:
    gates: list[dict[str, str]] = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        name, action, reason = line.split("|", 2)
        gates.append({"name": name, "action": action, "reason": reason})
    return gates


def job_action(
    *,
    filter_active: bool,
    fail_open: bool,
    tripwire_hit: bool,
    docs_only: bool,
) -> dict[str, str]:
    if fail_open or tripwire_hit or not filter_active:
        return {"action": "run", "reason": "fail_open_or_tripwire_or_no_filter"}
    if docs_only:
        return {"action": "skip", "reason": "docs_only"}
    return {"action": "run", "reason": "not_docs_only"}


def build_plan(meta: dict[str, Any], gates: list[dict[str, str]]) -> dict[str, Any]:
    filter_active = bool(meta["filter_active"])
    fail_open = bool(meta["fail_open"])
    tripwire_hit = bool(meta["tripwire_hit"])
    taxonomy: list[str] = list(meta["taxonomy"])
    docs_only = (
        filter_active
        and not fail_open
        and not tripwire_hit
        and taxonomy == ["docs"]
    )
    jobs = job_action(
        filter_active=filter_active,
        fail_open=fail_open,
        tripwire_hit=tripwire_hit,
        docs_only=docs_only,
    )
    return {
        "schema_version": 1,
        "stage": meta["stage"],
        "filter_active": filter_active,
        "diff_range": meta.get("diff_range") or "",
        "changed_files": meta.get("changed_files") or [],
        "taxonomy": taxonomy,
        "tripwire_hit": tripwire_hit,
        "fail_open": fail_open,
        "docs_only": docs_only,
        "gates": gates,
        "jobs": {
            "gates": {"action": "run"},
            "tests": jobs,
            "package-coverage": jobs,
            "integration": jobs,
        },
    }


def emit_github_summary(doc: dict[str, Any]) -> None:
    gates = doc["gates"]
    skipped = [g["name"] for g in gates if g["action"] == "skip"]
    run = [g["name"] for g in gates if g["action"] == "run"]
    print("::notice title=CI plan (diff-scoped)::")
    print("### CI plan (diff-scoped)")
    print(f"- filter_active: {doc['filter_active']}")
    print(f"- fail_open: {doc['fail_open']}")
    print(f"- tripwire_hit: {doc['tripwire_hit']}")
    print(f"- taxonomy: {', '.join(doc['taxonomy']) or '(none)'}")
    print(f"- docs_only: {doc['docs_only']}")
    print(f"- changed_files: {len(doc['changed_files'])}")
    print(f"- gates run: {len(run)} · skip: {len(skipped)}")
    for job, info in doc["jobs"].items():
        print(f"- job {job}: {info['action']} ({info.get('reason', '')})")
    if skipped:
        sample = ", ".join(skipped[:12])
        extra = f" … +{len(skipped) - 12}" if len(skipped) > 12 else ""
        print(f"\n**Skipped gates:** {sample}{extra}")


def main() -> int:
    meta = json.loads(sys.argv[1])
    doc = build_plan(meta, parse_gates())
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    output_path = meta.get("output_path") or ""
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)
    fmt = meta.get("format") or "json"
    if fmt == "json":
        sys.stdout.write(text)
    elif fmt == "github":
        emit_github_summary(doc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
