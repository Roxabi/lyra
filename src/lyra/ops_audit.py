"""ACL matrix audits used by ``lyra ops verify``.

Static checks over ``deploy/nats/acl-matrix.json`` contents. Kept separate
from ``cli_ops`` so the file-length gate stays under budget and the audit
helpers remain trivially unit-testable without touching Typer.
"""

from __future__ import annotations

LYRA_OWNED_IDENTITIES: frozenset[str] = frozenset(
    {
        "hub",
        "telegram-adapter",
        "discord-adapter",
        "tts-adapter",
        "stt-adapter",
    }
)

BARE_INBOX_PATTERNS: frozenset[str] = frozenset({"_INBOX.>", "_inbox.>"})


def audit_matrix_inbox_drift(
    identities: dict[str, dict],
) -> list[tuple[str, str, str]]:
    """Flag lyra-owned identities still holding a bare inbox wildcard.

    Returns a list of ``(identity, grant, direction)`` triples where
    ``direction`` is ``"publish"`` or ``"subscribe"``. Per-identity scoped
    grants (``_INBOX.<identity>.>``) are not flagged. Satellite identities
    are excluded — their narrowing is tracked via per-satellite PRs
    (ADR-047).
    """
    findings: list[tuple[str, str, str]] = []
    for name, spec in identities.items():
        if name not in LYRA_OWNED_IDENTITIES:
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
