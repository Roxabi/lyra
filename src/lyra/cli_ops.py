"""lyra ops — operational sanity checks.

`lyra ops verify` walks ``deploy/nats/acl-matrix.json`` and, per identity,
publishes on every allowed subject (expect success) plus one `lyra.verify.deny.*`
probe (expect permission violation). Reads ``NATS_URL``/``NATS_CA_CERT`` from
env; seeds from ``~/.lyra/nkeys/<id>.seed`` (override via ``--seeds-dir``).
"""

from __future__ import annotations

import asyncio
import json
import os
import stat as _stat
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

import typer
from nats.aio.client import Client as NATS

import nats
from roxabi_nats.connect import _build_tls_context

ops_app = typer.Typer(name="ops", help="Operational sanity checks.")

_DEFAULT_NATS_URL = "nats://localhost:4222"
_DEFAULT_MATRIX = "deploy/nats/acl-matrix.json"
_DEFAULT_SEEDS_DIR = "~/.lyra/nkeys"
_DENY_SUBJECT_PREFIX = "lyra.verify.deny"
_FLUSH_TIMEOUT = 2


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


def _read_seed(seed_path: Path) -> str:
    """Read an nkey seed from *seed_path* with the same hardening as roxabi-nats.

    O_NOFOLLOW + live fstat — rejects symlinks, non-files, and any
    group/world-readable mode. SystemExit on any failure (CLI semantics).
    """
    fd = -1
    try:
        fd = os.open(str(seed_path), os.O_RDONLY | os.O_NOFOLLOW)
        st = os.fstat(fd)
        if not _stat.S_ISREG(st.st_mode):
            raise typer.Exit(code=1)
        if st.st_mode & 0o777 & 0o077:
            raise typer.BadParameter(
                f"seed {seed_path.name!r} has unsafe permissions"
                f" {oct(st.st_mode & 0o777)} (use 0o600 or 0o400)"
            )
        with os.fdopen(fd, "r") as fh:
            fd = -1
            seed = fh.read().strip()
    except OSError as exc:
        if fd != -1:
            os.close(fd)
        raise typer.BadParameter(
            f"seed {seed_path.name!r} unreadable: {exc.strerror or exc}"
        )
    if not seed:
        raise typer.BadParameter(f"seed {seed_path.name!r} is empty")
    return seed


@asynccontextmanager
async def _identity_connection(
    nats_url: str, seed_path: Path, error_sink: list[str]
) -> AsyncIterator[NATS]:
    """Yield a NATS client authed as a specific identity.

    The seed is read and passed to ``nats.connect`` directly — no env
    mutation, so this is safe under concurrent ``asyncio.gather`` use.
    Permissions violations sent by the server arrive on the async error
    callback; we capture them in *error_sink* so callers can inspect
    them after a flush.
    """
    seed = _read_seed(seed_path)

    async def _err_cb(exc: Exception) -> None:
        error_sink.append(str(exc))

    kwargs: dict = {"error_cb": _err_cb, "nkeys_seed_str": seed}
    tls_ctx = _build_tls_context()
    if tls_ctx:
        kwargs["tls"] = tls_ctx
    nc = await nats.connect(nats_url, **kwargs)
    try:
        yield nc
    finally:
        await nc.drain()
        await nc.close()


def _is_permission_error(message: str) -> bool:
    msg = message.lower()
    return "permission" in msg and "publish" in msg


async def _probe(
    nc: NATS, subject: str, errors: list[str], *, expect_deny: bool
) -> tuple[bool, str]:
    """Publish on *subject* and report whether the outcome matched expectation.

    NATS ``-ERR`` frames arrive on the async error callback after flush, so
    we yield once before sampling *errors* to let already-buffered frames
    reach the callback.
    """
    before = len(errors)
    await nc.publish(subject, b"verify")
    try:
        await nc.flush(timeout=_FLUSH_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        return False, f"flush error: {exc}"
    await asyncio.sleep(0)
    denied = any(_is_permission_error(e) for e in errors[before:])
    if expect_deny:
        return (
            (True, "permission denied")
            if denied
            else (False, "publish accepted (expected deny)")
        )
    return (False, "permission denied") if denied else (True, "published")


async def _verify_identity(
    nats_url: str,
    name: str,
    spec: dict,
    seed_path: Path,
) -> IdentityResult:
    result = IdentityResult(identity=name)
    if not seed_path.is_file():
        result.skipped_reason = f"seed missing: {seed_path.name}"
        return result

    errors: list[str] = []
    try:
        async with _identity_connection(nats_url, seed_path, errors) as nc:
            for subject in spec.get("publish", []):
                expanded = _expand_subject(subject)
                ok, actual = await _probe(nc, expanded, errors, expect_deny=False)
                result.rows.append(
                    CheckRow(name, expanded, "pub", "published", actual, ok)
                )
            deny_subject = f"{_DENY_SUBJECT_PREFIX}.{name}"
            ok, actual = await _probe(nc, deny_subject, errors, expect_deny=True)
            result.rows.append(
                CheckRow(name, deny_subject, "deny", "permission denied", actual, ok)
            )
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        result.skipped_reason = f"connect failed: {exc}"
    return result


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


def _seed_path_for(seeds_dir: Path, name: str) -> Path:
    """Resolve and validate that ``{seeds_dir}/{name}.seed`` stays inside *seeds_dir*.

    Defends against malicious identity names from a tampered matrix file
    (``../etc/passwd``, absolute paths, …).
    """
    candidate = (seeds_dir / f"{name}.seed").resolve()
    base = seeds_dir.resolve()
    if not candidate.is_relative_to(base):
        raise typer.BadParameter(f"identity name {name!r} resolves outside seeds-dir")
    return candidate


async def _verify_all(
    nats_url: str, identities: dict[str, dict], seeds_dir: Path
) -> list[IdentityResult]:
    out: list[IdentityResult] = []
    for name, spec in identities.items():
        seed_path = _seed_path_for(seeds_dir, name)
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
        # `failed` is filtered by `first_failure`, so this is always non-None.
        first = failed[0].first_failure
        if first is not None:
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
