"""List small, unblocked, ready issues for the sequential automation sweep.

Usage:
    uv run python tools/sweep_candidates.py
    uv run python tools/sweep_candidates.py --json   # machine-readable output

/goal Small Issue Automation Sweep — v3 Sequential (1-at-a-time)
================================================================
Run a sequential sweep of small, unblocked, ready issues in roxabi/lyra.

Rules:
- Max 1 issue in flight at all times (1 worktree, 1 PR, no parallel batches)
- "Ready" = open + no open blockers (v6: no open blocks edge, direct or propagated)
- Issue number > 1000, size ∈ {XS, S, or unset}, priority P0→P3
- Skip if: worktree exists, open PR exists, or assigned to someone else

Workflow (single pipeline, no parallelism):
1. List candidates: `uv run python tools/sweep_candidates.py --json --top 1`
2. If no candidates → backoff 5min → 15min → 30min → max 1h, then reloop
3. If top 1 has no size → /issue-triage → reloop (may drop out if sized >S)
4. If top 1 is XS/S → /dev-core:dev for that single issue (exactly 1 worktree)
5. On completion → /pr --create, tag `reviewed` if auto-approved
6. /ci-watch on the active PR until completion
7. /pr --merge if `reviewed` label + CI green
8. Re-eval: immediately loop back to step 1 for next top 1

WIP gates (checked before any lane):
- worktree exists? → warn and block
- open PR for issue? → skip
- assigned to someone? → skip
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO = "roxabi/lyra"
MIN_NUMBER = 1000

# Priority sort order: lower = higher priority
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

# Size sort order for tie-breaking: no-size first, then XS, then S
SIZE_ORDER = {"XS": 1, "S": 2, None: 0}


@dataclass
class Candidate:
    number: int
    title: str
    priority: str | None
    size: str | None
    labels: list[str]
    assignees: list[str]
    body: str
    blocked_by: list[str]
    url: str

    def to_dict(self) -> dict:
        return asdict(self)

    def sort_key(self) -> tuple[int, int, int]:
        return (
            PRIORITY_ORDER.get(self.priority or "", 99),
            SIZE_ORDER.get(self.size, 99),
            self.number,
        )


def _gh_json(cmd: list[str]) -> list[dict]:
    result = subprocess.run(
        ["gh"] + cmd,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def fetch_issues() -> list[dict]:
    fields = "number,title,labels,body,assignees"
    return _gh_json(
        [
            "issue", "list",
            "--repo", REPO,
            "--state", "open",
            "--limit", "200",
            "--json", fields,
        ]
    )


def fetch_prs() -> list[dict]:
    """Open PRs to check for WIP overlap."""
    return _gh_json(
        [
            "pr", "list",
            "--repo", REPO,
            "--state", "open",
            "--json", "number,title,headRefName",
            "--limit", "100",
        ]
    )


def parse_size(labels: list[str]) -> str | None:
    for lbl in labels:
        if lbl.startswith("size:"):
            return lbl.split(":", 1)[1]
    return None


def parse_priority(labels: list[str]) -> str | None:
    for lbl in labels:
        if lbl.startswith("P") and lbl[1:].isdigit():
            return lbl
    return None


def parse_blocked_by(body: str) -> list[str]:
    """Extract blocked-by issue refs from body text.

    Matches patterns like:
      blocked by #1234
      blocked-by: #1234
      Blocked by #1234, #1235
    """
    refs: list[str] = []
    pattern = re.compile(r"#(\d+)", re.IGNORECASE)
    for line in body.splitlines():
        lower = line.lower()
        if "blocked by" in lower or "blocked-by" in lower:
            refs.extend(pattern.findall(line))
    return [f"#{r}" for r in refs]


def is_blocked(issue: dict, all_issues: dict[int, dict]) -> bool:
    """Check if any blocked_by refs are still open."""
    blocked_by = parse_blocked_by(issue.get("body", ""))
    for ref in blocked_by:
        num = int(ref.lstrip("#"))
        target = all_issues.get(num)
        if target is None:
            # Unknown issue — conservatively treat as open blocker
            return True
        if target.get("state") != "closed":
            return True
    return False


def has_open_pr_for_issue(issue_number: int, prs: list[dict]) -> bool:
    """Heuristic: check if any open PR branch mentions the issue number."""
    for pr in prs:
        branch = pr.get("headRefName", "")
        title = pr.get("title", "")
        text = f"{branch} {title}"
        if str(issue_number) in text:
            return True
    return False


def worktree_exists() -> bool:
    """Check if any .claude/worktrees/ directory exists for lyra."""
    wt_root = Path(__file__).resolve().parents[1] / ".claude" / "worktrees"
    if not wt_root.exists():
        return False
    return any(wt_root.iterdir())


def get_worktree_blockers(issues: list[dict]) -> list[dict]:
    """Return worktrees with linked issue info."""
    wt_root = Path(__file__).resolve().parents[1] / ".claude" / "worktrees"
    if not wt_root.exists():
        return []

    blockers = []
    issues_by_number = {i["number"]: i for i in issues}

    for wt in wt_root.iterdir():
        if not wt.is_dir():
            continue
        match = re.match(r"(\d+)", wt.name)
        if not match:
            continue
        issue_num = int(match.group(1))
        issue = issues_by_number.get(issue_num)
        if issue:
            labels = [lbl["name"] for lbl in issue.get("labels", [])]
            blockers.append({
                "wt_name": wt.name,
                "issue_number": issue_num,
                "issue_title": issue.get("title", ""),
                "size": parse_size(labels),
                "priority": parse_priority(labels),
            })
    return blockers


def get_pr_blockers(issues: list[dict], prs: list[dict]) -> list[dict]:
    """Return open PRs with linked issue info."""
    blockers = []
    issues_by_number = {i["number"]: i for i in issues}

    for pr in prs:
        branch = pr.get("headRefName", "")
        title = pr.get("title", "")
        text = f"{branch} {title}"
        match = re.search(r"#?(\d{4,})", text)
        if not match:
            continue
        issue_num = int(match.group(1))
        issue = issues_by_number.get(issue_num)
        if issue:
            labels = [lbl["name"] for lbl in issue.get("labels", [])]
            blockers.append({
                "pr_number": pr.get("number"),
                "pr_title": title,
                "issue_number": issue_num,
                "issue_title": issue.get("title", ""),
                "size": parse_size(labels),
                "priority": parse_priority(labels),
            })
    return blockers


def filter_candidates(
    issues: list[dict],
    prs: list[dict],
) -> list[Candidate]:
    all_issues_by_number = {i["number"]: i for i in issues}
    candidates: list[Candidate] = []

    for iss in issues:
        number = iss["number"]
        if number <= MIN_NUMBER:
            continue

        labels = [lbl["name"] for lbl in iss.get("labels", [])]
        size = parse_size(labels)
        priority = parse_priority(labels)
        assignees = [a["login"] for a in iss.get("assignees", [])]

        # Size filter: must be XS, S, or unset
        if size and size not in ("XS", "S"):
            continue

        # Blocker filter: no open blockers
        if is_blocked(iss, all_issues_by_number):
            continue

        # WIP gate: skip if assigned to someone else
        if assignees:
            continue

        # WIP gate: skip if open PR likely linked
        if has_open_pr_for_issue(number, prs):
            continue

        candidates.append(
            Candidate(
                number=number,
                title=iss["title"],
                priority=priority,
                size=size,
                labels=labels,
                assignees=assignees,
                body=iss.get("body", ""),
                blocked_by=parse_blocked_by(iss.get("body", "")),
                url=iss.get("url", f"https://github.com/{REPO}/issues/{number}"),
            )
        )

    # Sort: P0→P3, then no-size→XS→S, then number
    candidates.sort(key=lambda c: c.sort_key())
    return candidates


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="List sweep candidates")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--top", type=int, default=10, help="Max candidates to show")
    parser.add_argument(
        "--goal", action="store_true", help="Echo the /goal procedure and exit"
    )
    args = parser.parse_args()

    if args.goal:
        print(__doc__)
        return 0

    issues = fetch_issues()
    prs = fetch_prs()
    candidates = filter_candidates(issues, prs)

    # WIP gate: worktree + PR blocker info
    wt_blockers = get_worktree_blockers(issues)
    pr_blockers = get_pr_blockers(issues, prs)

    if not args.json:
        for wt in wt_blockers:
            size = wt["size"] or "unset"
            flag = "🟢" if size in ("XS", "S") else "⚠️"
            msg = (
                f"{flag} Worktree `{wt['wt_name']}` → #{wt['issue_number']} "
                f"(size: {size}) — sweep blocked"
            )
            print(msg, file=sys.stderr)

        for pr in pr_blockers:
            size = pr["size"] or "unset"
            flag = "🟢" if size in ("XS", "S") else "⚠️"
            msg = (
                f"{flag} PR #{pr['pr_number']} `{pr['pr_title']}` → "
                f"#{pr['issue_number']} (size: {size}) — sweep blocked"
            )
            print(msg, file=sys.stderr)

        if not wt_blockers and worktree_exists():
            print(
                "⚠️  Worktree exists (unlinked) — sweep blocked until cleared.",
                file=sys.stderr,
            )

    top = candidates[: args.top]

    if args.json:
        print(json.dumps([c.to_dict() for c in top], indent=2))
    else:
        print(f"{'#':>5}  {'P':>3}  {'Size':>5}  {'Title'}")
        print("-" * 70)
        for c in top:
            p = c.priority or "-"
            s = c.size or "-"
            blockers = (
                f"  [blocked by {', '.join(c.blocked_by)}]"
                if c.blocked_by
                else ""
            )
            print(f"{c.number:>5}  {p:>3}  {s:>5}  {c.title}{blockers}")
        print("-" * 70)
        print(f"Total candidates: {len(candidates)}  (showing top {len(top)})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
