"""Tests for tools/render_quadlet.py — BotStore-backed (#1417).

Tests invoke the script as a subprocess:
  python tools/render_quadlet.py --platform <telegram|discord>
                                  --db <path>
                                  --tmpl <path>
                                  --dest <path>

No mocking of store internals — real tmp_path + BotStore throughout.
"""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
import sys
import textwrap
from pathlib import Path

from lyra.core.agent.bot_models import BotRow
from lyra.infrastructure.stores.bot_store import BotStore
from tests.helpers.bot_store import db_upsert

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "render_quadlet.py"

# ── fixture helpers ────────────────────────────────────────────────────────────


def make_bot_db(tmp_path: Path, bots: list[BotRow]) -> Path:
    """Create a BotStore database at tmp_path/config.db and seed it with *bots*."""
    db_path = tmp_path / "config.db"
    # Ensure the schema exists even when *bots* is empty
    _ensure_bot_db(db_path)
    for bot in bots:
        db_upsert(db_path, bot)
    return db_path


def _ensure_bot_db(db_path: Path) -> None:
    """Create an empty BotStore database file with schema."""

    async def _run() -> None:
        store = BotStore(db_path=str(db_path))
        await store.connect()
        await store.close()

    asyncio.run(_run())


def make_tmpl(tmp_path: Path, *, with_marker: bool = True) -> Path:
    """Write a minimal .container.tmpl file.

    with_marker=True  → contains exactly one {{bot_secrets}} line
    with_marker=False → marker absent (triggers the missing-marker error path)
    """
    path = tmp_path / "lyra-telegram.container.tmpl"
    body = textwrap.dedent("""\
        [Unit]
        Description=lyra-telegram adapter

        [Container]
        Image=ghcr.io/roxabi/factory:latest
        Secret=lyra-nats-creds,type=mount,target=nats_creds,mode=0400,uid=1500,gid=1500
        {marker}

        [Service]
        Restart=always
        """)
    marker_line = "{{bot_secrets}}" if with_marker else "# no marker here"
    path.write_text(body.format(marker=marker_line))
    return path


def _run_render(
    args: list[str],
) -> subprocess.CompletedProcess[str]:
    """Invoke render_quadlet.py via subprocess; capture stdout+stderr."""
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=30,
    )


# ── tests ──────────────────────────────────────────────────────────────────────


def test_happy_path(tmp_path: Path) -> None:
    """Two bots → two Secret= lines, sorted by bot_id, marker gone, byte-stable.

    Spec trace: A6(a)
    Negative sentinel: if sort_bots is deleted, aryl would appear AFTER lyra
    (insertion order), breaking the sorted-order assertion.
    If the marker substitution is skipped, {{bot_secrets}} remains in output,
    breaking the marker-absence assertion.
    """
    # Arrange — bots deliberately out of alphabetical order (lyra before aryl)
    db = make_bot_db(
        tmp_path,
        bots=[
            BotRow(platform="telegram", bot_id="lyra", agent="lyra_default"),
            BotRow(platform="telegram", bot_id="aryl", agent="lyra_default"),
        ],
    )
    tmpl = make_tmpl(tmp_path, with_marker=True)
    dest = tmp_path / "lyra-telegram.container"

    # Act (run 1)
    result1 = _run_render(
        [
            "--platform",
            "telegram",
            "--db",
            str(db),
            "--tmpl",
            str(tmpl),
            "--dest",
            str(dest),
        ]
    )

    # Assert exit 0
    assert result1.returncode == 0, (
        f"Expected exit 0; got {result1.returncode}\n"
        f"stdout: {result1.stdout}\nstderr: {result1.stderr}"
    )
    assert dest.exists(), "dest file must be written"

    content = dest.read_text()

    # Two token Secret= lines — one per bot (excludes webhook lines)
    token_lines = [ln for ln in content.splitlines() if "target=bot_token-" in ln]
    assert len(token_lines) == 2, (
        f"Expected exactly 2 bot_token- Secret= lines; got {token_lines!r}"
    )

    # Each bot's Secret= line has the full required attributes
    for bot_id in ("aryl", "lyra"):
        expected = (
            f"Secret=factory-bot-telegram-{bot_id},"
            f"type=mount,"
            f"target=bot_token-{bot_id},"
            f"mode=0400,"
            f"uid=1500,"
            f"gid=1500"
        )
        assert expected in content, (
            f"Missing Secret= directive for {bot_id!r}.\nExpected line: {expected!r}\n"
            f"Got content:\n{content}"
        )

    # aryl must appear BEFORE lyra (sorted order)
    idx_aryl = content.index("Secret=factory-bot-telegram-aryl")
    idx_lyra = content.index("Secret=factory-bot-telegram-lyra")
    assert idx_aryl < idx_lyra, (
        "Secret= lines must be sorted by bot_id: aryl before lyra\n"
        f"aryl at {idx_aryl}, lyra at {idx_lyra}\n{content}"
    )

    # Marker must be gone
    assert "{{bot_secrets}}" not in content, (
        "{{bot_secrets}} marker must be replaced in rendered output"
    )

    # Byte-stability: run a second time with identical input
    sha1 = hashlib.sha256(dest.read_bytes()).hexdigest()
    result2 = _run_render(
        [
            "--platform",
            "telegram",
            "--db",
            str(db),
            "--tmpl",
            str(tmpl),
            "--dest",
            str(dest),
        ]
    )
    assert result2.returncode == 0, f"Second run failed: {result2.stderr}"
    sha2 = hashlib.sha256(dest.read_bytes()).hexdigest()
    assert sha1 == sha2, (
        f"Render is not byte-stable across runs: sha1={sha1} sha2={sha2}"
    )


def test_empty_bots(tmp_path: Path) -> None:
    """No bots in BotStore → marker replaced by empty string, exit 0.

    Spec trace: A6(b)
    Negative sentinel: if the empty-list path is not handled, the renderer might
    leave {{bot_secrets}} in the output or crash with a KeyError/AttributeError.
    """
    db = make_bot_db(tmp_path, bots=[])
    tmpl = make_tmpl(tmp_path, with_marker=True)
    dest = tmp_path / "lyra-telegram.container"

    result = _run_render(
        [
            "--platform",
            "telegram",
            "--db",
            str(db),
            "--tmpl",
            str(tmpl),
            "--dest",
            str(dest),
        ]
    )

    assert result.returncode == 0, (
        f"Expected exit 0 for empty bot list; got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert dest.exists(), "dest file must be written even when bot list is empty"

    content = dest.read_text()

    # No orphan marker
    assert "{{bot_secrets}}" not in content, (
        "{{bot_secrets}} marker must be replaced (with empty string) "
        "when bot list is empty"
    )

    # No Secret=factory-bot-telegram-* lines
    secret_lines = [
        ln for ln in content.splitlines() if "Secret=factory-bot-telegram-" in ln
    ]
    assert secret_lines == [], (
        "Expected no Secret=factory-bot-telegram-* lines for empty bot list; "
        f"got {secret_lines!r}"
    )


def test_missing_marker(tmp_path: Path) -> None:
    """Template without {{bot_secrets}} marker → non-zero exit with error message.

    Spec trace: A6(c)
    Negative sentinel: if the missing-marker guard is deleted, the renderer would
    silently emit an unchanged template (no bot secrets injected), exit 0, and
    the operator would deploy a broken Quadlet without any signal.
    """
    db = make_bot_db(
        tmp_path,
        bots=[BotRow(platform="telegram", bot_id="lyra", agent="lyra_default")],
    )
    tmpl = make_tmpl(tmp_path, with_marker=False)
    dest = tmp_path / "lyra-telegram.container"

    result = _run_render(
        [
            "--platform",
            "telegram",
            "--db",
            str(db),
            "--tmpl",
            str(tmpl),
            "--dest",
            str(dest),
        ]
    )

    # Assert non-zero exit
    assert result.returncode != 0, (
        f"Expected non-zero exit when marker is missing; got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # dest must NOT be written when the marker is missing
    assert not dest.exists(), "dest must NOT be written when the marker is missing"

    # Error message must mention the missing marker
    combined = result.stdout + result.stderr
    assert "bot_secrets" in combined or "marker" in combined.lower(), (
        "Error message must reference the missing marker.\n"
        f"Combined output: {combined!r}"
    )


def test_missing_db(tmp_path: Path) -> None:
    """DB path that does not exist → non-zero exit with error message.

    Spec trace: A6(d) — migrated from config.toml to BotStore.
    Negative sentinel: if the db-not-found guard is deleted, aiosqlite would
    raise an unhandled error, producing an ugly traceback with exit 1 — but the
    spec requires a CLEAR error message.
    """
    db = tmp_path / "nonexistent-config.db"
    tmpl = make_tmpl(tmp_path, with_marker=True)
    dest = tmp_path / "lyra-telegram.container"

    result = _run_render(
        [
            "--platform",
            "telegram",
            "--db",
            str(db),
            "--tmpl",
            str(tmpl),
            "--dest",
            str(dest),
        ]
    )

    # Assert non-zero exit
    assert result.returncode != 0, (
        f"Expected non-zero exit for missing db; got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Error message must reference the missing path — clean message, no raw traceback.
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined, (
        f"Expected clean error message; got Python traceback.\nCombined: {combined!r}"
    )
    assert "database" in combined.lower() or "not found" in combined.lower(), (
        "Error message must reference the missing db path.\n"
        f"Combined output: {combined!r}"
    )


def test_webhook_enabled_happy_path(tmp_path: Path) -> None:
    """Bot with webhook_enabled = true → emits both bot_token- and bot_webhook- lines.

    Spec trace: #1373 happy path webhook
    Negative sentinel: if the webhook branch is absent, only the token line appears
    and the bot_webhook- assertion fails.
    """
    db = make_bot_db(
        tmp_path,
        bots=[
            BotRow(
                platform="telegram",
                bot_id="lyra",
                agent="lyra_default",
                webhook_enabled=True,
            )
        ],
    )
    tmpl = make_tmpl(tmp_path, with_marker=True)
    dest = tmp_path / "lyra-telegram.container"

    result = _run_render(
        [
            "--platform",
            "telegram",
            "--db",
            str(db),
            "--tmpl",
            str(tmpl),
            "--dest",
            str(dest),
        ]
    )

    assert result.returncode == 0, (
        f"Expected exit 0; got {result.returncode}\nstderr: {result.stderr}"
    )
    content = dest.read_text()

    expected_token = (
        "Secret=factory-bot-telegram-lyra,"
        "type=mount,"
        "target=bot_token-lyra,"
        "mode=0400,"
        "uid=1500,"
        "gid=1500"
    )
    expected_webhook = (
        "Secret=factory-bot-telegram-lyra-webhook,"
        "type=mount,"
        "target=bot_webhook-lyra,"
        "mode=0400,"
        "uid=1500,"
        "gid=1500"
    )
    assert expected_token in content, (
        f"Missing bot_token- Secret= line.\nContent:\n{content}"
    )
    assert expected_webhook in content, (
        f"Missing bot_webhook- Secret= line.\nContent:\n{content}"
    )

    # webhook line must appear immediately after the token line (grouped per bot)
    token_idx = content.index(expected_token)
    webhook_idx = content.index(expected_webhook)
    assert token_idx < webhook_idx, (
        "bot_token- line must precede bot_webhook- line in output"
    )


def test_webhook_enabled_false_no_webhook_line(tmp_path: Path) -> None:
    """Bot with webhook_enabled = False → only bot_token- line emitted.

    Spec trace: #1373 regression — default false
    Negative sentinel: if webhook_enabled defaults to True, this test fails on the
    no-webhook-line assertion.
    """
    db = make_bot_db(
        tmp_path,
        bots=[
            BotRow(
                platform="telegram",
                bot_id="lyra",
                agent="lyra_default",
                webhook_enabled=False,
            )
        ],
    )
    tmpl = make_tmpl(tmp_path, with_marker=True)
    dest = tmp_path / "lyra-telegram.container"

    result = _run_render(
        [
            "--platform",
            "telegram",
            "--db",
            str(db),
            "--tmpl",
            str(tmpl),
            "--dest",
            str(dest),
        ]
    )

    assert result.returncode == 0, (
        f"Expected exit 0; got {result.returncode}\nstderr: {result.stderr}"
    )
    content = dest.read_text()

    assert "bot_token-lyra" in content, "bot_token- line must be present"
    assert "bot_webhook-lyra" not in content, (
        "bot_webhook- line must NOT appear when webhook_enabled is False"
    )


def test_webhook_mixed_bots(tmp_path: Path) -> None:
    """Bot A (webhook=True) + Bot B (webhook=False) → webhook only for A.

    Spec trace: #1373 mixed bots
    Negative sentinel: if webhook_enabled is ignored and always emitted, bot_webhook-b
    appears and the assertion fails.
    """
    db = make_bot_db(
        tmp_path,
        bots=[
            BotRow(
                platform="telegram",
                bot_id="a",
                agent="lyra_default",
                webhook_enabled=True,
            ),
            BotRow(
                platform="telegram",
                bot_id="b",
                agent="lyra_default",
                webhook_enabled=False,
            ),
        ],
    )
    tmpl = make_tmpl(tmp_path, with_marker=True)
    dest = tmp_path / "lyra-telegram.container"

    result = _run_render(
        [
            "--platform",
            "telegram",
            "--db",
            str(db),
            "--tmpl",
            str(tmpl),
            "--dest",
            str(dest),
        ]
    )

    assert result.returncode == 0, (
        f"Expected exit 0; got {result.returncode}\nstderr: {result.stderr}"
    )
    content = dest.read_text()

    assert "bot_webhook-a" in content, (
        "bot_webhook-a must appear for webhook_enabled bot"
    )
    assert "bot_webhook-b" not in content, (
        "bot_webhook-b must NOT appear for bot without webhook_enabled"
    )
    # Both token lines must be present
    assert "bot_token-a" in content
    assert "bot_token-b" in content


def test_webhook_enabled_discord(tmp_path: Path) -> None:
    """Discord bot with webhook_enabled = true → emits bot_webhook- for discord.

    render_secrets() is platform-agnostic via {platform} substitution. This test
    confirms the discord branch substitutes correctly:
    factory-bot-discord-<bot_id>-webhook, target=bot_webhook-<bot_id>.

    Spec trace: #1398 G12 — Discord platform parity
    Negative sentinel: if render_secrets() hard-codes 'telegram' instead of using
    the platform variable, the discord secret name would contain 'telegram' and the
    expected_webhook assertion would fail.
    """
    db = make_bot_db(
        tmp_path,
        bots=[
            BotRow(
                platform="discord",
                bot_id="lyra",
                agent="lyra_default",
                webhook_enabled=True,
            )
        ],
    )
    tmpl_path = tmp_path / "lyra-discord.container.tmpl"
    tmpl_path.write_text(
        "[Unit]\nDescription=lyra-discord adapter\n\n[Container]\n{{bot_secrets}}\n"
    )
    dest = tmp_path / "lyra-discord.container"

    result = _run_render(
        [
            "--platform",
            "discord",
            "--db",
            str(db),
            "--tmpl",
            str(tmpl_path),
            "--dest",
            str(dest),
        ]
    )

    assert result.returncode == 0, (
        f"Expected exit 0; got {result.returncode}\nstderr: {result.stderr}"
    )
    content = dest.read_text()

    expected_webhook = (
        "Secret=factory-bot-discord-lyra-webhook,"
        "type=mount,"
        "target=bot_webhook-lyra,"
        "mode=0400,"
        "uid=1500,"
        "gid=1500"
    )
    assert expected_webhook in content, (
        f"Missing discord bot_webhook- Secret= line.\nExpected: {expected_webhook!r}\n"
        f"Content:\n{content}"
    )
    assert "target=bot_token-lyra" in content, (
        "bot_token- line must also be present for discord bot"
    )
