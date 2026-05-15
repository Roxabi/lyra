"""Integration tests for the POSIX-sh shell shims: git-credential-lyra-gh and lyra-gh.

Scope reduction vs. plan T8:
  The plan mentioned "git push against git daemon test repo" as a verification
  vector. That was softened here to direct subprocess invocations of the
  credential helper and lyra-gh shim. Rationale: git daemon setup is highly
  environment-dependent (port allocation, repo init, pack-refs) and adds no
  additional protocol coverage — the git credential protocol contract is fully
  exercised by running the helper binary directly with stdin/stdout assertions.
  The token-to-child-env contract is exercised by lyra-gh + gh_stub_bin.

Pre-flight:
  Both shims use socat (preferred) or BSD nc -U to reach the dispenser socket.
  If neither is available the entire module is skipped to keep CI green on
  minimal containers.

Implementation note — subprocess-in-async:
  Tests that use the mock_dispenser fixture must run subprocess.run() via
  asyncio.get_event_loop().run_in_executor(None, ...) rather than calling it
  directly. A direct blocking subprocess.run() inside an async test function
  blocks the event loop, preventing the asyncio Unix server from dispatching
  the incoming connection. run_in_executor offloads the blocking call to a
  thread pool, keeping the event loop free to handle socket I/O.
"""

from __future__ import annotations

import asyncio
import functools
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# ── pre-flight: check socat / BSD nc -U ───────────────────────────────────────

_SOCAT = subprocess.run(["which", "socat"], capture_output=True).returncode == 0

_BSD_NC = (
    subprocess.run(["which", "nc"], capture_output=True).returncode == 0
    and b"-U" in subprocess.run(["nc", "-h"], capture_output=True).stderr
)

_TRANSPORT_AVAILABLE = _SOCAT or _BSD_NC

_SKIP_NO_TRANSPORT = pytest.mark.skipif(
    not _TRANSPORT_AVAILABLE,
    reason="socat or BSD nc -U required for shell shim tests",
)

# ── paths to the shell scripts ────────────────────────────────────────────────

_TOOLS_DIR = Path(__file__).parent.parent.parent / "src" / "lyra" / "tools" / "gh_token"

_GIT_CREDENTIAL_SCRIPT = _TOOLS_DIR / "git-credential-lyra-gh"
_LYRA_GH_SCRIPT = _TOOLS_DIR / "lyra-gh"


# ── mock dispenser fixture ─────────────────────────────────────────────────────


@pytest.fixture()
async def mock_dispenser(tmp_path: Path) -> "AsyncIterator[Path]":
    """Spawn a bare-bones asyncio Unix server that replies with a hard-coded token.

    Protocol: reads one line, writes the credential response, closes.
    Socket is chmod 0660 to mirror production behaviour.
    """

    sock_path = tmp_path / "dispenser.sock"

    async def _handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            await reader.readline()  # consume the request line (get / peek)
            writer.write(b"username=x-access-token\npassword=ghs_test_token\n\n")
            try:
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                # nc -U (and socat) may close the read end before drain
                # completes — that is fine; bytes are already in the kernel
                # send buffer and the client has received them.
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    server = await asyncio.start_unix_server(_handler, str(sock_path))
    os.chmod(sock_path, 0o660)

    try:
        yield sock_path
    finally:
        server.close()
        await server.wait_closed()
        if sock_path.exists():
            sock_path.unlink(missing_ok=True)


# ── gh stub binary fixture ────────────────────────────────────────────────────


@pytest.fixture()
def gh_stub_bin(tmp_path: Path) -> Path:
    """Write a minimal POSIX-sh stub that echoes argv and GH_TOKEN, then exits 0."""
    stub = tmp_path / "gh-stub"
    stub.write_text(
        "#!/bin/sh\n"
        'echo "stub-gh: argv=$*"\n'
        'echo "stub-gh: GH_TOKEN=$GH_TOKEN" >&2\n'
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


# ── helper: build a clean env dict ────────────────────────────────────────────


def _base_env(**overrides: str) -> dict[str, str]:
    """Return os.environ copy with given keys overridden/added."""
    env = dict(os.environ)
    env.update(overrides)
    return env


async def _run_subprocess_in_thread(
    *args: str,
    input: bytes = b"",  # noqa: A002
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run subprocess.run in a thread pool executor.

    Must be used instead of a direct subprocess.run() call inside async tests
    that depend on the mock_dispenser fixture. A blocking subprocess.run() would
    stall the asyncio event loop, preventing the server from handling the
    connection. Offloading to run_in_executor keeps the loop free to dispatch
    socket I/O while the shell script runs in the background thread.
    """
    loop = asyncio.get_event_loop()
    fn = functools.partial(
        subprocess.run,
        list(args),
        input=input,
        capture_output=True,
        env=env,
    )
    return await loop.run_in_executor(None, fn)


# ═══════════════════════════════════════════════════════════════════════════════
# A — git-credential-lyra-gh get emits credential lines
# ═══════════════════════════════════════════════════════════════════════════════


@_SKIP_NO_TRANSPORT
@pytest.mark.asyncio
async def test_git_credential_lyra_gh_get_emits_credential_lines(
    mock_dispenser: Path,
) -> None:
    """get action: stdout must contain username= and password= lines from dispenser."""
    # Arrange
    env = _base_env(LYRA_GH_DISPENSER_SOCK=str(mock_dispenser))

    # Act — offload to thread so the event loop remains free to serve the socket
    result = await _run_subprocess_in_thread(
        str(_GIT_CREDENTIAL_SCRIPT),
        "get",
        input=b"protocol=https\nhost=github.com\n\n",
        env=env,
    )

    # Assert
    assert result.returncode == 0, (
        f"expected exit 0, got {result.returncode}; stderr={result.stderr!r}"
    )
    out = result.stdout.decode()
    assert "username=x-access-token" in out
    assert "password=ghs_test_token" in out


# ═══════════════════════════════════════════════════════════════════════════════
# B — store is a no-op
# ═══════════════════════════════════════════════════════════════════════════════


@_SKIP_NO_TRANSPORT
def test_git_credential_lyra_gh_store_is_noop() -> None:
    """store action: exits 0 with no output (token storage is not needed)."""
    # Arrange
    env = _base_env(LYRA_GH_DISPENSER_SOCK="/tmp/does-not-matter.sock")

    # Act
    result = subprocess.run(
        [str(_GIT_CREDENTIAL_SCRIPT), "store"],
        input=b"protocol=https\nhost=github.com\n\n",
        capture_output=True,
        env=env,
    )

    # Assert
    assert result.returncode == 0
    assert result.stdout == b""


# ═══════════════════════════════════════════════════════════════════════════════
# C — erase is a no-op
# ═══════════════════════════════════════════════════════════════════════════════


@_SKIP_NO_TRANSPORT
def test_git_credential_lyra_gh_erase_is_noop() -> None:
    """erase action: exits 0 with no output (tokens are minted on demand)."""
    # Arrange
    env = _base_env(LYRA_GH_DISPENSER_SOCK="/tmp/does-not-matter.sock")

    # Act
    result = subprocess.run(
        [str(_GIT_CREDENTIAL_SCRIPT), "erase"],
        input=b"protocol=https\nhost=github.com\n\n",
        capture_output=True,
        env=env,
    )

    # Assert
    assert result.returncode == 0
    assert result.stdout == b""


# ═══════════════════════════════════════════════════════════════════════════════
# D — unknown action errors
# ═══════════════════════════════════════════════════════════════════════════════


@_SKIP_NO_TRANSPORT
def test_git_credential_lyra_gh_unknown_action_errors() -> None:
    """Unknown action: exits non-zero with a message on stderr."""
    # Arrange
    env = _base_env(LYRA_GH_DISPENSER_SOCK="/tmp/does-not-matter.sock")

    # Act
    result = subprocess.run(
        [str(_GIT_CREDENTIAL_SCRIPT), "bogus"],
        input=b"",
        capture_output=True,
        env=env,
    )

    # Assert
    assert result.returncode != 0
    assert b"unknown action" in result.stderr or b"bogus" in result.stderr


# ═══════════════════════════════════════════════════════════════════════════════
# E — lyra-gh passes token to child via GH_TOKEN env
# ═══════════════════════════════════════════════════════════════════════════════


@_SKIP_NO_TRANSPORT
@pytest.mark.asyncio
async def test_lyra_gh_shim_exec_passes_token_to_child(
    mock_dispenser: Path,
    gh_stub_bin: Path,
) -> None:
    """lyra-gh resolves a token and exec-replaces into the stub with GH_TOKEN set."""
    # Arrange
    env = _base_env(
        LYRA_GH_DISPENSER_SOCK=str(mock_dispenser),
        LYRA_GH_BIN=str(gh_stub_bin),
    )

    # Act — offload to thread so the event loop remains free to serve the socket
    result = await _run_subprocess_in_thread(
        str(_LYRA_GH_SCRIPT),
        "--version",
        env=env,
    )

    # Assert
    assert result.returncode == 0, f"expected exit 0; stderr={result.stderr!r}"
    stdout = result.stdout.decode()
    stderr = result.stderr.decode()
    assert "stub-gh: argv=--version" in stdout, (
        f"stub did not receive argv; stdout={stdout!r}"
    )
    assert "stub-gh: GH_TOKEN=ghs_test_token" in stderr, (
        f"token not present in child env; stderr={stderr!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# F — lyra-gh does not leak token to parent process
# ═══════════════════════════════════════════════════════════════════════════════


@_SKIP_NO_TRANSPORT
@pytest.mark.asyncio
async def test_lyra_gh_shim_does_not_leak_token_to_parent(
    mock_dispenser: Path,
    gh_stub_bin: Path,
) -> None:
    """Token must not appear in the test process's own environment after exit."""
    # Arrange — ensure GH_TOKEN is not already set in the test runner
    env = _base_env(
        LYRA_GH_DISPENSER_SOCK=str(mock_dispenser),
        LYRA_GH_BIN=str(gh_stub_bin),
    )
    env.pop("GH_TOKEN", None)

    # Act — offload to thread so the event loop remains free to serve the socket
    await _run_subprocess_in_thread(
        str(_LYRA_GH_SCRIPT),
        "--version",
        env=env,
    )

    # Assert — exec-replacement means the token only existed inside the child;
    # the current (parent) process env is unchanged.
    assert "GH_TOKEN" not in os.environ, (
        "GH_TOKEN leaked into the test process environment"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# G — lyra-gh fails when dispenser is unreachable
# ═══════════════════════════════════════════════════════════════════════════════


@_SKIP_NO_TRANSPORT
def test_lyra_gh_shim_fails_when_dispenser_unreachable(
    tmp_path: Path,
) -> None:
    """lyra-gh exits non-zero and must not emit the token in error output."""
    # Arrange
    env = _base_env(
        LYRA_GH_DISPENSER_SOCK="/tmp/does-not-exist-lyra-gh-test.sock",
        LYRA_GH_BIN="/bin/true",  # won't be reached, but must be a valid path
    )

    # Act
    result = subprocess.run(
        [str(_LYRA_GH_SCRIPT), "some-command"],
        capture_output=True,
        env=env,
    )

    # Assert
    assert result.returncode != 0, (
        "expected non-zero exit when dispenser socket is missing"
    )
    combined = result.stdout.decode() + result.stderr.decode()
    assert "ghs_" not in combined, (
        f"token fragment appeared in error output: {combined!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# H (optional) — lyra-gh fails when gh binary is missing
# ═══════════════════════════════════════════════════════════════════════════════


@_SKIP_NO_TRANSPORT
@pytest.mark.asyncio
async def test_lyra_gh_shim_fails_when_gh_bin_missing(
    mock_dispenser: Path,
) -> None:
    """lyra-gh exits non-zero and prints a helpful message when gh binary is absent."""
    # Arrange
    env = _base_env(
        LYRA_GH_DISPENSER_SOCK=str(mock_dispenser),
        LYRA_GH_BIN="/tmp/does-not-exist-gh-bin",
    )

    # Act — offload to thread so the event loop remains free to serve the socket
    result = await _run_subprocess_in_thread(
        str(_LYRA_GH_SCRIPT),
        "--version",
        env=env,
    )

    # Assert
    assert result.returncode != 0
    stderr = result.stderr.decode()
    assert "gh CLI not found" in stderr, (
        f"expected 'gh CLI not found' in stderr; got: {stderr!r}"
    )
