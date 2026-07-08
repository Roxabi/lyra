"""ACL matrix audits used by ``factory ops verify``.

Static checks over ``deploy/nats/acl-matrix.json`` contents. Kept separate
from ``cli_ops`` so the file-length gate stays under budget and the audit
helpers remain trivially unit-testable without touching Typer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FACTORY_OWNED_IDENTITIES: frozenset[str] = frozenset(
    {
        "hub",
        "telegram-adapter",
        "discord-adapter",
        "tts-adapter",
        "stt-adapter",
    }
)

BARE_INBOX_PATTERNS: frozenset[str] = frozenset({"_INBOX.>", "_inbox.>"})


@dataclass
class CheckRow:
    identity: str
    subject: str
    kind: str  # "pub" | "deny"
    expected: str
    actual: str
    ok: bool


@dataclass
class IdentityResult:
    identity: str
    rows: list[CheckRow] = field(default_factory=list)
    skipped_reason: str | None = None

    @property
    def pub_passed(self) -> int:
        return sum(1 for r in self.rows if r.kind == "pub" and r.ok)

    @property
    def deny_passed(self) -> int:
        return sum(1 for r in self.rows if r.kind == "deny" and r.ok)

    @property
    def first_failure(self) -> CheckRow | None:
        return next((r for r in self.rows if not r.ok), None)


def active_identities(identities: dict[str, dict]) -> dict[str, dict]:
    """Identities rendered in live auth.conf (``status != \"retired\"``).

    Mirrors ``scripts._effective.effective_grants`` retirement filter — retired
    rows stay in the matrix for history but are not NATS-authenticated.
    """
    return {
        name: spec
        for name, spec in identities.items()
        if spec.get("status") != "retired"
    }


def audit_matrix_inbox_drift(
    identities: dict[str, dict],
) -> list[tuple[str, str, str]]:
    """Flag lyra-owned identities still holding a bare inbox wildcard.

    Returns a list of ``(identity, grant, direction)`` triples where
    ``direction`` is ``"publish"`` or ``"subscribe"``. Per-identity scoped
    grants (``_INBOX.<identity>.>``) are not flagged. Satellite identities
    are excluded — their narrowing is tracked via per-satellite PRs
    (ADR-047 (absorbed into ADR-045)).
    """
    findings: list[tuple[str, str, str]] = []
    for name, spec in identities.items():
        if name not in FACTORY_OWNED_IDENTITIES:
            continue
        for grant in spec.get("publish", []):
            if grant in BARE_INBOX_PATTERNS:
                findings.append((name, grant, "publish"))
        for grant in spec.get("subscribe", []):
            if grant in BARE_INBOX_PATTERNS:
                findings.append((name, grant, "subscribe"))
    return findings


def format_drift_finding(finding: tuple[str, str, str]) -> str:
    """Human-readable single-line rendering of a drift triple."""
    identity, grant, direction = finding
    if direction == "publish":
        verb = "publishes on"
    else:
        verb = "subscribes to"
    return f"DRIFT: {identity} still {verb} {grant} — should be _INBOX.{identity}.>"


def emit_drift_report(identities: dict[str, dict], echo) -> bool:
    """Emit one formatted line per drift finding; return True if any."""
    drift = audit_matrix_inbox_drift(identities)
    for finding in drift:
        echo(format_drift_finding(finding), err=True)
    return bool(drift)


def print_verify_report(results: list[IdentityResult], echo) -> int:
    """Summarize verify results; return 0 on full pass, 1 if any fail or skip."""
    total_pub_pass = sum(r.pub_passed for r in results)
    total_pub = sum(1 for r in results for c in r.rows if c.kind == "pub")
    total_deny_pass = sum(r.deny_passed for r in results)
    total_deny = sum(1 for r in results for c in r.rows if c.kind == "deny")
    skipped = [r for r in results if r.skipped_reason]
    failed = [r for r in results if r.first_failure]

    for r in skipped:
        echo(f"SKIP {r.identity}: {r.skipped_reason}")

    if failed and (first := failed[0].first_failure):
        echo(
            f"FAIL {first.identity} {first.kind} {first.subject} — "
            f"expected {first.expected!r}, got {first.actual!r}"
        )

    echo(
        f"{len(results)} identities, "
        f"{total_pub_pass}/{total_pub} pub checks passed, "
        f"{total_deny_pass}/{total_deny} deny checks passed"
        + (f", {len(skipped)} skipped" if skipped else "")
    )
    return 1 if failed or skipped else 0
