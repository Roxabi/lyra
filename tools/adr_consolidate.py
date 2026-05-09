#!/usr/bin/env python3
"""
adr_consolidate.py — Batch-update ADR .mdx files with machine-readable frontmatter
and redirect banners, then rebuild meta.json grouped by domain.

Usage: uv run python tools/adr_consolidate.py [--dry-run]
"""

import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
ADR_DIR = REPO_ROOT / "docs" / "architecture" / "adr"
MATRIX_FILE = REPO_ROOT / "artifacts" / "analyses" / "adr-consolidation-matrix.md"

DOMAIN_TO_DOC = {
    "MSG": "messaging.md",
    "LLM": "llm-streaming.md",
    "ADP": "adapters.md",
    "STO": "storage.md",
    "SEC": "security-routing.md",
    "DEP": "deployment.md",
    "CTR": "contracts.md",
    "ENG": "architecture-patterns.md",
    "WRK": "workers-tooling.md",
    "OUT": None,  # special case
}

# Domain display names for meta.json section headers
DOMAIN_LABELS = {
    "MSG": "Messaging & NATS",
    "LLM": "LLM, Streaming & Agents",
    "ADP": "Adapters",
    "STO": "Storage",
    "SEC": "Security & Routing",
    "DEP": "Deployment",
    "CTR": "Contracts",
    "ENG": "Architecture Patterns",
    "WRK": "Workers & Tooling",
    "OUT": "Out of Scope",
}

# Domain ordering for meta.json
DOMAIN_ORDER = ["MSG", "LLM", "ADP", "STO", "SEC", "DEP", "CTR", "ENG", "WRK", "OUT"]

# Sentinel to detect already-present banner (anchored ID)
BANNER_SENTINEL = "{#current-truth}"
OUT_SENTINEL = "Out of lyra scope"

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_matrix(matrix_path: Path) -> dict[str, dict]:
    """Parse the ## Domain mapping table from the matrix file into a per-ADR dict."""
    text = matrix_path.read_text(encoding="utf-8")

    # Find the ## Domain mapping section
    section_match = re.search(r"^## Domain mapping\s*$", text, re.MULTILINE)
    if not section_match:
        raise ValueError("Could not find '## Domain mapping' section in matrix file")

    section_text = text[section_match.end() :]

    # Find the table — rows starting with |
    table_rows = re.findall(r"^\|.*\|$", section_text, re.MULTILINE)

    # Skip header and separator rows (separator has dashes)
    data_rows = [r for r in table_rows if not re.match(r"^\|[-| ]+\|$", r)]
    # Skip the header row (first row with column names)
    if data_rows and "ADR" in data_rows[0]:
        data_rows = data_rows[1:]

    mapping: dict[str, dict] = {}
    for row in data_rows:
        cols = [c.strip() for c in row.strip("|").split("|")]
        if len(cols) < 4:
            continue
        adr_raw, _title, domain, status = cols[0], cols[1], cols[2], cols[3]
        # ADR column may be "001" or just "1"
        try:
            adr_num = int(adr_raw)
        except ValueError:
            continue
        adr_id = f"{adr_num:03d}"
        mapping[adr_id] = {
            "domain": domain.strip(),
            "status": status.strip().lower(),  # "accepted" or "amended"
        }

    return mapping


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------


def find_adr_file(adr_id: str) -> Path | None:
    """Find the .mdx file for a given ADR id like '001'."""
    matches = list(ADR_DIR.glob(f"{adr_id}-*.mdx"))
    if not matches:
        return None
    return matches[0]


def parse_frontmatter(content: str) -> tuple[str, str, str] | None:
    """
    Returns (fm_block, fm_inner, rest) where:
      fm_block = the full '---\\n...\\n---\\n' block (including delimiters)
      fm_inner = the YAML content between the delimiters
      rest     = everything after the closing ---
    Returns None if no frontmatter found.
    """
    m = re.match(r"^(---\n(.*?)\n---\n)(.*)", content, re.DOTALL)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def ensure_status_in_frontmatter(
    fm_inner: str, desired_status: str
) -> tuple[str, bool]:
    """
    Ensure `status: <desired_status>` is in frontmatter YAML.
    Returns (new_fm_inner, changed).
    """
    status_pattern = re.compile(r"^status:\s*.+$", re.MULTILINE)
    if status_pattern.search(fm_inner):
        new_fm = status_pattern.sub(f"status: {desired_status}", fm_inner)
        changed = new_fm != fm_inner
        return new_fm, changed
    else:
        # Append before end
        new_fm = fm_inner.rstrip("\n") + f"\nstatus: {desired_status}"
        return new_fm, True


def build_banner(domain: str) -> str:
    """Build the redirect banner for a given domain."""
    if domain == "OUT":
        msg = "**Out of lyra scope** — current truth lives in the `roxabi-forge` repo."
        return f"\n> {msg}\n\n"
    doc = DOMAIN_TO_DOC.get(domain)
    if doc is None:
        raise KeyError(f"unknown domain: {domain}")
    doc_stem = doc[:-3]  # strip .md
    href = f"[`docs/architecture/{doc}`](../{doc_stem}.md)"
    line1 = f"> **Current truth** → {href}{{#current-truth}}"
    line2 = (
        "> This ADR is preserved as a decision record. "
        "For up-to-date state and invariants, read the domain page."
    )
    return f"\n{line1}\n{line2}\n\n"


def banner_already_present(rest: str, domain: str) -> bool:
    """Check if the banner is already present in the body."""
    if domain == "OUT":
        return OUT_SENTINEL in rest
    return BANNER_SENTINEL in rest


def process_adr(adr_id: str, info: dict, dry_run: bool = False) -> str:
    """
    Process a single ADR file. Returns one of: "updated", "already-current", "skipped".
    """
    path = find_adr_file(adr_id)
    if path is None:
        return "skipped"

    content = path.read_text(encoding="utf-8")
    parsed = parse_frontmatter(content)

    if parsed is None:
        # No frontmatter — skip defensively
        print(f"  WARN: {adr_id} has no frontmatter, skipping")
        return "skipped"

    _fm_block, fm_inner, rest = parsed
    domain = info["domain"]
    desired_status = info["status"]

    # Step 1: ensure status in frontmatter
    new_fm_inner, fm_changed = ensure_status_in_frontmatter(fm_inner, desired_status)

    # Step 2: ensure banner present
    banner = build_banner(domain)
    banner_present = banner_already_present(rest, domain)

    if not fm_changed and banner_present:
        return "already-current"

    # Rebuild file
    new_fm_block = f"---\n{new_fm_inner}\n---\n"
    if not banner_present:
        new_rest = banner + rest
    else:
        new_rest = rest

    new_content = new_fm_block + new_rest

    if not dry_run:
        path.write_text(new_content, encoding="utf-8")

    return "updated"


# ---------------------------------------------------------------------------
# meta.json
# ---------------------------------------------------------------------------


def stem_for(adr_id: str) -> str | None:
    """Return the page stem (filename without .mdx) for an ADR id."""
    path = find_adr_file(adr_id)
    if path is None:
        return None
    return path.stem


def build_meta_json(mapping: dict[str, dict]) -> dict:
    """Build the meta.json structure grouped by domain."""
    # Group ADR ids by domain, ascending order
    by_domain: dict[str, list[str]] = {d: [] for d in DOMAIN_ORDER}
    for adr_id, info in sorted(mapping.items()):
        domain = info["domain"]
        if domain not in by_domain:
            by_domain[domain] = []
        by_domain[domain].append(adr_id)

    pages: list[str] = []
    for domain in DOMAIN_ORDER:
        adr_ids = by_domain.get(domain, [])
        if not adr_ids:
            continue

        # Filter to only ADRs with files on disk
        stems = []
        for adr_id in adr_ids:
            stem = stem_for(adr_id)
            if stem:
                stems.append(stem)

        if not stems:
            continue

        label = DOMAIN_LABELS[domain]
        pages.append(f"---{label}---")
        pages.extend(stems)

    description = "Historical decision records. For current state see ../<domain>.md."
    return {
        "title": "ADRs (decision archive)",
        "description": description,
        "pages": pages,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        print("DRY RUN — no files will be written\n")

    print(f"Reading matrix from {MATRIX_FILE}")
    mapping = parse_matrix(MATRIX_FILE)
    print(f"Parsed {len(mapping)} ADR entries from matrix\n")

    stats = {"updated": 0, "already-current": 0, "skipped": 0}

    for adr_id in sorted(mapping.keys()):
        info = mapping[adr_id]
        result = process_adr(adr_id, info, dry_run=dry_run)
        stats[result] += 1
        status_char = {"updated": "U", "already-current": ".", "skipped": "-"}[result]
        d, s = info["domain"], info["status"]
        print(f"  [{status_char}] ADR-{adr_id}  domain={d}  status={s}  → {result}")

    u, c, k = stats["updated"], stats["already-current"], stats["skipped"]
    print(f"\nSummary: {u} updated | {c} already-current | {k} skipped")

    # Rebuild meta.json
    print("\nRebuilding meta.json...")
    meta = build_meta_json(mapping)
    meta_path = ADR_DIR / "meta.json"
    if not dry_run:
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"meta.json written: {len(meta['pages'])} entries ({meta_path})")


if __name__ == "__main__":
    main()
