"""E2E parity test: boots nats-server with rendered auth.conf.

Verifies identity authorization end-to-end.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Generator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

NATS_AVAILABLE = shutil.which("nats-server") is not None
NK_AVAILABLE = shutil.which("nk") is not None

_nats_py_available = False
try:
    import nats as _nats_module  # noqa: F401  # pyright: ignore[reportUnusedImport]

    _nats_py_available = True
except ImportError:
    pass

NATS_PY_AVAILABLE: bool = _nats_py_available

pytestmark = pytest.mark.skipif(
    not (NATS_AVAILABLE and NK_AVAILABLE),
    reason="nats-server and nk must be on PATH — CI installs both",
)


# ── Module-scoped fixtures ────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def rendered_auth_conf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Render auth.conf + seed files; return the directory.

    Writes:
      <tmp>/auth.conf         — nats-server authorization config
      <tmp>/<name>.seed       — raw nk seed bytes per active identity
    """
    from scripts._loader import load_matrix
    from scripts._nk import SubprocessNkeyProvider
    from scripts._renderer import render_auth_conf

    tmp = tmp_path_factory.mktemp("nats")
    matrix_path = REPO_ROOT / "tests/scripts/fixtures/v2-prod.json"
    matrix = load_matrix(matrix_path)

    provider = SubprocessNkeyProvider()
    active = {
        name: ident
        for name, ident in matrix["identities"].items()
        if ident["status"] == "active"
    }
    seeds = {name: provider.gen_seed(name) for name in active}
    pubkeys = {name: provider.pubkey_from_seed(seed) for name, seed in seeds.items()}
    text = render_auth_conf(matrix, pubkeys)

    (tmp / "auth.conf").write_text(text)
    for name, seed in seeds.items():
        (tmp / f"{name}.seed").write_bytes(seed + b"\n")
    return tmp


@pytest.fixture(scope="module")
def nats_server(rendered_auth_conf: Path) -> Generator[None, None, None]:
    """Start nats-server with rendered auth.conf; shut down after tests."""
    proc = subprocess.Popen(
        [
            "nats-server",
            "-c",
            str(rendered_auth_conf / "auth.conf"),
            "-m",
            "8222",
            "-p",
            "4223",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    # Poll healthz with 3s timeout
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen("http://localhost:8222/healthz", timeout=0.5)
            break
        except OSError:
            time.sleep(0.05)
    else:
        proc.terminate()
        raw = proc.stderr.read() if proc.stderr else b""
        stderr_log = raw.decode(errors="replace")
        pytest.fail(f"nats-server did not start within 3s\nstderr:\n{stderr_log}")

    yield

    proc.terminate()
    proc.wait(timeout=5)


# ── Tests: structural (no live server needed) ─────────────────────────────────


def test_rendered_auth_conf_parses_correctly(rendered_auth_conf: Path) -> None:
    """parse_auth_conf round-trips the rendered file; user count matches active."""
    from scripts._loader import load_matrix
    from scripts._renderer import parse_auth_conf

    matrix = load_matrix(REPO_ROOT / "tests/scripts/fixtures/v2-prod.json")
    active_count = sum(
        1 for ident in matrix["identities"].values() if ident["status"] == "active"
    )

    text = (rendered_auth_conf / "auth.conf").read_text()
    parsed = parse_auth_conf(text)

    assert len(parsed.users) == active_count, (
        f"Expected {active_count} users in auth.conf, got {len(parsed.users)}"
    )


def test_hub_identity_in_auth_conf(rendered_auth_conf: Path) -> None:
    """Rendered auth.conf contains a user entry whose comment_name is 'hub'."""
    from scripts._renderer import parse_auth_conf

    text = (rendered_auth_conf / "auth.conf").read_text()
    parsed = parse_auth_conf(text)

    names = [u.comment_name for u in parsed.users]
    assert "hub" in names, f"'hub' not found in auth.conf users; got: {names}"


def test_retired_identity_excluded(tmp_path: Path) -> None:
    """Render with v2-with-retired fixture; 'old-worker' must not appear."""
    from scripts._loader import load_matrix
    from scripts._nk import FakeNkeyProvider
    from scripts._renderer import render_auth_conf

    matrix_path = FIXTURES_DIR / "v2-with-retired.json"
    matrix = load_matrix(matrix_path)

    provider = FakeNkeyProvider()
    active = {
        name: ident
        for name, ident in matrix["identities"].items()
        if ident["status"] == "active"
    }
    pubkeys = {
        name: provider.pubkey_from_seed(provider.gen_seed(name)) for name in active
    }
    text = render_auth_conf(matrix, pubkeys)

    # The retired identity name must not appear as a comment or nkey entry
    assert "old-worker" not in text, (
        "Retired identity 'old-worker' found in rendered auth.conf"
    )


def test_inbox_grant_derived_from_flows(rendered_auth_conf: Path) -> None:
    """clipool-worker publish allow includes '_inbox.hub.>' from hub flow."""
    from scripts._renderer import parse_auth_conf

    text = (rendered_auth_conf / "auth.conf").read_text()
    parsed = parse_auth_conf(text)

    clipool = next(
        (u for u in parsed.users if u.comment_name == "clipool-worker"), None
    )
    assert clipool is not None, "clipool-worker not found in auth.conf users"
    got = clipool.publish_allow
    assert "_inbox.hub.>" in got, (
        f"Expected '_inbox.hub.>' in clipool-worker publish_allow; got: {got}"
    )


# ── Tests: live server ────────────────────────────────────────────────────────


def test_nats_server_starts_with_auth_conf(nats_server: None) -> None:
    """nats-server booted and healthz returns HTTP 200."""
    resp = urllib.request.urlopen("http://localhost:8222/healthz", timeout=2)
    assert resp.status == 200, f"healthz returned {resp.status}"


@pytest.mark.skipif(
    not NATS_PY_AVAILABLE,
    reason="nats-py not installed — skipping live connection test",
)
def test_hub_can_connect(nats_server: None, rendered_auth_conf: Path) -> None:
    """hub identity connects to nats-server using the seed that was registered."""
    import asyncio

    import nats

    # nkeys_seed reads the file raw (no strip); use nkeys_seed_str with stripped content
    # to avoid binascii.Error from a trailing newline in the .seed file.
    seed_str = (rendered_auth_conf / "hub.seed").read_text().strip()

    async def _connect() -> None:
        nc = await nats.connect(
            "nats://localhost:4223",
            nkeys_seed_str=seed_str,
        )
        await nc.drain()

    asyncio.run(_connect())


@pytest.mark.skipif(
    not NATS_PY_AVAILABLE,
    reason="nats-py not installed — skipping live connection test",
)
def test_voice_tts_can_connect(nats_server: None, rendered_auth_conf: Path) -> None:
    """voice-tts identity connects to nats-server using its registered seed."""
    import asyncio

    import nats

    seed_str = (rendered_auth_conf / "voice-tts.seed").read_text().strip()

    async def _connect() -> None:
        nc = await nats.connect(
            "nats://localhost:4223",
            nkeys_seed_str=seed_str,
        )
        await nc.drain()

    asyncio.run(_connect())


@pytest.mark.skipif(
    not NATS_PY_AVAILABLE,
    reason="nats-py not installed — skipping live connection test",
)
def test_clipool_worker_can_connect(
    nats_server: None, rendered_auth_conf: Path
) -> None:
    """clipool-worker identity connects to nats-server using its registered seed."""
    import asyncio

    import nats

    seed_str = (rendered_auth_conf / "clipool-worker.seed").read_text().strip()

    async def _connect() -> None:
        nc = await nats.connect(
            "nats://localhost:4223",
            nkeys_seed_str=seed_str,
        )
        await nc.drain()

    asyncio.run(_connect())


@pytest.mark.skipif(
    not NATS_PY_AVAILABLE,
    reason="nats-py not installed — skipping live ACL test",
)
def test_hub_publish_acl_enforced(nats_server: None, rendered_auth_conf: Path) -> None:
    """hub: allowed publish succeeds; denied publish triggers Permissions Violation."""
    import asyncio

    import nats

    seed_str = (rendered_auth_conf / "hub.seed").read_text().strip()
    acl_errors: list[Exception] = []

    async def _test() -> None:
        async def error_cb(exc: Exception) -> None:
            acl_errors.append(exc)

        nc = await nats.connect(
            "nats://localhost:4223",
            nkeys_seed_str=seed_str,
            error_cb=error_cb,
        )
        await nc.publish("lyra.clipool.cmd", b"ping")
        await nc.publish("lyra.inbound.telegram.acl_test", b"denied")
        await asyncio.sleep(0.2)
        await nc.drain()

    asyncio.run(_test())
    perm_violations = [
        e for e in acl_errors if "permissions violation" in str(e).lower()
    ]
    assert perm_violations, (
        f"Expected Permissions Violation for denied publish; errors: {acl_errors}"
    )


@pytest.mark.skipif(
    not NATS_PY_AVAILABLE,
    reason="nats-py not installed — skipping live ACL test",
)
def test_voice_tts_publish_acl_enforced(
    nats_server: None, rendered_auth_conf: Path
) -> None:
    """voice-tts: allowed publish succeeds; denied publish is rejected."""
    import asyncio

    import nats

    seed_str = (rendered_auth_conf / "voice-tts.seed").read_text().strip()
    acl_errors: list[Exception] = []

    async def _test() -> None:
        async def error_cb(exc: Exception) -> None:
            acl_errors.append(exc)

        nc = await nats.connect(
            "nats://localhost:4223",
            nkeys_seed_str=seed_str,
            error_cb=error_cb,
        )
        await nc.publish("lyra.voice.tts.heartbeat", b"ping")
        await nc.publish("lyra.clipool.cmd", b"denied")
        await asyncio.sleep(0.2)
        await nc.drain()

    asyncio.run(_test())
    perm_violations = [
        e for e in acl_errors if "permissions violation" in str(e).lower()
    ]
    assert perm_violations, (
        f"Expected Permissions Violation for denied publish; errors: {acl_errors}"
    )


@pytest.mark.skipif(
    not NATS_PY_AVAILABLE,
    reason="nats-py not installed — skipping live ACL test",
)
def test_clipool_worker_subscribe_acl_enforced(
    nats_server: None, rendered_auth_conf: Path
) -> None:
    """clipool-worker: allowed subscribe succeeds; denied subscribe is rejected."""
    import asyncio

    import nats

    seed_str = (rendered_auth_conf / "clipool-worker.seed").read_text().strip()
    acl_errors: list[Exception] = []

    async def _test() -> None:
        async def error_cb(exc: Exception) -> None:
            acl_errors.append(exc)

        nc = await nats.connect(
            "nats://localhost:4223",
            nkeys_seed_str=seed_str,
            error_cb=error_cb,
        )
        await nc.subscribe("lyra.clipool.cmd")
        await nc.subscribe("lyra.inbound.discord.>")
        await asyncio.sleep(0.2)
        await nc.drain()

    asyncio.run(_test())
    perm_violations = [
        e for e in acl_errors if "permissions violation" in str(e).lower()
    ]
    assert perm_violations, (
        f"Expected Permissions Violation for denied subscribe; errors: {acl_errors}"
    )


def test_retired_identity_connect_rejected(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Retired identity seed is not in auth.conf; connection must be rejected."""
    import asyncio
    import urllib.request

    if not (NATS_PY_AVAILABLE and NATS_AVAILABLE and NK_AVAILABLE):
        pytest.skip("nats-server, nk, and nats-py all required")

    from scripts._loader import load_matrix
    from scripts._nk import SubprocessNkeyProvider
    from scripts._renderer import render_auth_conf

    import nats

    tmp = tmp_path_factory.mktemp("nats_retired")
    matrix_path = FIXTURES_DIR / "v2-with-retired.json"
    matrix = load_matrix(matrix_path)

    provider = SubprocessNkeyProvider()
    # Generate seeds for ALL identities including retired so we can attempt connect
    seeds = {name: provider.gen_seed(name) for name in matrix["identities"]}
    # auth.conf only includes active identities
    active_pubkeys = {
        name: provider.pubkey_from_seed(seeds[name])
        for name, ident in matrix["identities"].items()
        if ident["status"] == "active"
    }
    text = render_auth_conf(matrix, active_pubkeys)
    (tmp / "auth.conf").write_text(text)
    for name, seed in seeds.items():
        (tmp / f"{name}.seed").write_bytes(seed + b"\n")

    proc = subprocess.Popen(
        ["nats-server", "-c", str(tmp / "auth.conf"), "-m", "8223", "-p", "4224"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen("http://localhost:8223/healthz", timeout=0.5)
            break
        except OSError:
            time.sleep(0.05)
    else:
        proc.terminate()
        raw = proc.stderr.read() if proc.stderr else b""
        stderr_text = raw.decode(errors="replace")
        pytest.fail(f"nats-server (retired test) did not start\n{stderr_text}")

    try:
        seed_str = (tmp / "old-worker.seed").read_text().strip()

        async def _connect_retired() -> None:
            await nats.connect(
                "nats://localhost:4224",
                nkeys_seed_str=seed_str,
                connect_timeout=2,
            )

        with pytest.raises(Exception):
            asyncio.run(_connect_retired())
    finally:
        proc.terminate()
        proc.wait(timeout=5)
