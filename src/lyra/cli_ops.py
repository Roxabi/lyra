"""lyra ops — operational sanity checks.

`lyra ops verify` walks the IDENTITIES × allow-list matrix from
``deploy/nats/acl-matrix.json`` and, for each identity, exercises the
NATS server's ACL by:

  - publishing on every allowed subject     → expect success
  - publishing on a representative deny     → expect a NATS permissions
                                              violation

Reports a PASS/FAIL summary. Exit 0 = all checks pass, exit 1 = first
offending row printed.

Reads ``NATS_URL`` and ``NATS_CA_CERT`` from the environment, same as
the rest of Lyra. Per-identity nkey seeds are picked up from
``~/.lyra/nkeys/<identity>.seed`` (override via ``--seeds-dir``).
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

import typer
from nats.aio.client import Client as NATS

from roxabi_nats.connect import nats_connect

ops_app = typer.Typer(name="ops", help="Operational sanity checks.")

_DEFAULT_NATS_URL = "nats://localhost:4222"
_DEFAULT_MATRIX = "deploy/nats/acl-matrix.json"
_DEFAULT_SEEDS_DIR = "~/.lyra/nkeys"
_DENY_SUBJECT_PREFIX = "lyra.verify.deny"
_FLUSH_TIMEOUT = 2


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Matrix loading + subject expansion
# ---------------------------------------------------------------------------


def _load_matrix(path: Path) -> dict[str, dict]:
    """Read acl-matrix.json and return its ``identities`` map."""
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        raise typer.BadParameter(f"matrix file not found: {path}")
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"matrix file is not valid JSON: {exc}")
    identities = raw.get("identities")
    if not isinstance(identities, dict) or not identities:
        raise typer.BadParameter(f"matrix file has no `identities` map: {path}")
    return identities


def _expand_subject(subject: str) -> str:
    """Replace NATS wildcards with a concrete `verify` token.

    ``foo.>`` → ``foo.verify`` | ``foo.*.bar`` → ``foo.verify.bar``
    """
    return ".".join(
        "verify" if tok in (">", "*") else tok for tok in subject.split(".")
    )


# ---------------------------------------------------------------------------
# Per-identity NATS connection
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _identity_connection(
    nats_url: str, seed_path: Path, error_sink: list[str]
) -> AsyncIterator[NATS]:
    """Yield a NATS client authed as a specific identity.

    Permissions violations sent by the server arrive on the async error
    callback — we capture them in *error_sink* so the caller can inspect
    them after a flush.
    """
    prev_seed = os.environ.get("NATS_NKEY_SEED_PATH")
    os.environ["NATS_NKEY_SEED_PATH"] = str(seed_path)

    async def _err_cb(exc: Exception) -> None:
        error_sink.append(str(exc))

    try:
        nc = await nats_connect(nats_url, error_cb=_err_cb)
    finally:
        if prev_seed is None:
            os.environ.pop("NATS_NKEY_SEED_PATH", None)
        else:
            os.environ["NATS_NKEY_SEED_PATH"] = prev_seed
    try:
        yield nc
    finally:
        await nc.drain()
        await nc.close()


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


def _is_permission_error(message: str) -> bool:
    msg = message.lower()
    return "permission" in msg and "publish" in msg


async def _probe_pub(nc: NATS, subject: str, errors: list[str]) -> tuple[bool, str]:
    """Publish on *subject*; return (ok, actual)."""
    before = len(errors)
    await nc.publish(subject, b"verify")
    try:
        await nc.flush(timeout=_FLUSH_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        return False, f"flush error: {exc}"
    new = errors[before:]
    if any(_is_permission_error(e) for e in new):
        return False, "permission denied"
    return True, "published"


async def _probe_deny(nc: NATS, subject: str, errors: list[str]) -> tuple[bool, str]:
    """Publish on a subject expected to be denied; return (ok, actual)."""
    before = len(errors)
    await nc.publish(subject, b"verify")
    try:
        await nc.flush(timeout=_FLUSH_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        return False, f"flush error: {exc}"
    new = errors[before:]
    if any(_is_permission_error(e) for e in new):
        return True, "permission denied"
    return False, "publish accepted (expected deny)"


async def _verify_identity(
    nats_url: str,
    name: str,
    spec: dict,
    seed_path: Path,
) -> IdentityResult:
    result = IdentityResult(identity=name)
    if not seed_path.is_file():
        result.skipped_reason = f"seed missing: {seed_path}"
        return result

    errors: list[str] = []
    try:
        async with _identity_connection(nats_url, seed_path, errors) as nc:
            for subject in spec.get("publish", []):
                expanded = _expand_subject(subject)
                ok, actual = await _probe_pub(nc, expanded, errors)
                result.rows.append(
                    CheckRow(name, expanded, "pub", "published", actual, ok)
                )
            deny_subject = f"{_DENY_SUBJECT_PREFIX}.{name}"
            ok, actual = await _probe_deny(nc, deny_subject, errors)
            result.rows.append(
                CheckRow(name, deny_subject, "deny", "permission denied", actual, ok)
            )
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        result.skipped_reason = f"connect failed: {exc}"
    return result


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@ops_app.command("verify")
def verify(
    matrix: Path = typer.Option(  # noqa: B008
        Path(_DEFAULT_MATRIX),
        "--matrix",
        help="Path to acl-matrix.json.",
    ),
    seeds_dir: Path = typer.Option(  # noqa: B008
        Path(_DEFAULT_SEEDS_DIR),
        "--seeds-dir",
        help="Directory containing per-identity nkey seeds.",
    ),
    nats_url: str = typer.Option(  # noqa: B008
        None,
        "--nats-url",
        help="NATS URL (default: NATS_URL env var, then nats://localhost:4222).",
    ),
    only: list[str] = typer.Option(  # noqa: B008
        None,
        "--only",
        help="Restrict verification to specific identity names (repeatable).",
    ),
) -> None:
    """Verify each identity's NATS publish ACL via the live server."""
    resolved_url = nats_url or os.environ.get("NATS_URL", _DEFAULT_NATS_URL)
    matrix_path = matrix.expanduser()
    seeds_path = seeds_dir.expanduser()
    identities = _load_matrix(matrix_path)
    if only:
        unknown = [n for n in only if n not in identities]
        if unknown:
            raise typer.BadParameter(f"unknown identity name(s): {', '.join(unknown)}")
        identities = {n: identities[n] for n in only}

    results = asyncio.run(_verify_all(resolved_url, identities, seeds_path))
    exit_code = _print_report(results)
    raise typer.Exit(exit_code)


async def _verify_all(
    nats_url: str, identities: dict[str, dict], seeds_dir: Path
) -> list[IdentityResult]:
    out: list[IdentityResult] = []
    for name, spec in identities.items():
        seed_path = seeds_dir / f"{name}.seed"
        out.append(await _verify_identity(nats_url, name, spec, seed_path))
    return out


def _print_report(results: list[IdentityResult]) -> int:
    total_pub_pass = sum(r.pub_passed for r in results)
    total_pub = sum(1 for r in results for c in r.rows if c.kind == "pub")
    total_deny_pass = sum(r.deny_passed for r in results)
    total_deny = sum(1 for r in results for c in r.rows if c.kind == "deny")
    skipped = [r for r in results if r.skipped_reason]
    failed = [r for r in results if r.first_failure]

    for r in skipped:
        typer.echo(f"SKIP {r.identity}: {r.skipped_reason}")

    if failed:
        first = failed[0].first_failure
        assert first is not None
        typer.echo(
            f"FAIL {first.identity} {first.kind} {first.subject} — "
            f"expected {first.expected!r}, got {first.actual!r}"
        )

    typer.echo(
        f"{len(results)} identities, "
        f"{total_pub_pass}/{total_pub} pub checks passed, "
        f"{total_deny_pass}/{total_deny} deny checks passed"
        + (f", {len(skipped)} skipped" if skipped else "")
    )
    return 1 if failed or skipped else 0
