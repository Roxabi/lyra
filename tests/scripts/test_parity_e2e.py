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
        (tmp / f"{name}.seed").write_bytes(seed)
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

    seed = (rendered_auth_conf / "hub.seed").read_bytes()

    async def _connect() -> None:
        nc = await nats.connect(
            "nats://localhost:4223",
            nkeys_seed=seed,
        )
        await nc.drain()

    asyncio.run(_connect())
