"""RED tests for tools/render_quadlet.py — #1369 T1.

These tests FAIL at collection or execution time because tools/render_quadlet.py
does not exist yet.  That is the intended RED state.

Tests invoke the script as a subprocess:
  python tools/render_quadlet.py --platform <telegram|discord>
                                  --config <path>
                                  --tmpl <path>
                                  --dest <path>

No mocking of TOML parsing or filesystem — real tmp_path throughout.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "render_quadlet.py"

# ── fixture helpers ────────────────────────────────────────────────────────────


def make_config(tmp_path: Path, bots: list[str] | None = None) -> Path:
    """Write a minimal config.toml with [[auth.telegram_bots]] entries.

    bots: list of bot_id strings.  None → omit the key entirely (no section).
    Empty list → write an empty inline array (auth.telegram_bots = []).
    """
    path = tmp_path / "config.toml"
    if bots is None:
        path.write_text("[auth]\n")
    elif len(bots) == 0:
        path.write_text("[auth]\ntelegram_bots = []\n")
    else:
        lines = ["[auth]\n"]
        for bot_id in bots:
            lines.append(f'[[auth.telegram_bots]]\nbot_id = "{bot_id}"\n\n')
        path.write_text("".join(lines))
    return path


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
        Image=ghcr.io/roxabi/lyra:latest
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
    (TOML order), breaking the sorted-order assertion.
    If the marker substitution is skipped, {{bot_secrets}} remains in output,
    breaking the marker-absence assertion.
    """
    # Arrange
    # bots listed deliberately out of alphabetical order — lyra before aryl — so that a
    # missing sort_bots() call would produce out-of-order output and fail the
    # sort assertion.
    config = make_config(tmp_path, bots=["lyra", "aryl"])
    tmpl = make_tmpl(tmp_path, with_marker=True)
    dest = tmp_path / "lyra-telegram.container"

    # Act (run 1)
    result1 = _run_render(
        [
            "--platform",
            "telegram",
            "--config",
            str(config),
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
            f"Secret=lyra-bot-telegram-{bot_id},"
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
    idx_aryl = content.index("Secret=lyra-bot-telegram-aryl")
    idx_lyra = content.index("Secret=lyra-bot-telegram-lyra")
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
            "--config",
            str(config),
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
    """No [[auth.telegram_bots]] entries → marker replaced by empty string, exit 0.

    Spec trace: A6(b)
    Negative sentinel: if the empty-list path is not handled, the renderer might
    leave {{bot_secrets}} in the output or crash with a KeyError/AttributeError.
    """
    # Arrange — config.toml with empty bot list
    config = make_config(tmp_path, bots=[])
    tmpl = make_tmpl(tmp_path, with_marker=True)
    dest = tmp_path / "lyra-telegram.container"

    # Act
    result = _run_render(
        [
            "--platform",
            "telegram",
            "--config",
            str(config),
            "--tmpl",
            str(tmpl),
            "--dest",
            str(dest),
        ]
    )

    # Assert exit 0
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

    # No Secret=lyra-bot-telegram-* lines
    secret_lines = [
        ln for ln in content.splitlines() if "Secret=lyra-bot-telegram-" in ln
    ]
    assert secret_lines == [], (
        "Expected no Secret=lyra-bot-telegram-* lines for empty bot list; "
        f"got {secret_lines!r}"
    )


def test_missing_bots_key(tmp_path: Path) -> None:
    """[[auth.telegram_bots]] key absent entirely → same as empty list (exit 0).

    No Secret= lines produced when the key is missing.

    Spec trace: implicit — auth.get(key, []) contract.
    """
    # writes [auth]\n only, no telegram_bots key
    config = make_config(tmp_path, bots=None)
    tmpl = make_tmpl(tmp_path, with_marker=True)
    dest = tmp_path / "lyra-telegram.container"

    result = _run_render(
        [
            "--platform",
            "telegram",
            "--config",
            str(config),
            "--tmpl",
            str(tmpl),
            "--dest",
            str(dest),
        ]
    )

    assert result.returncode == 0, (
        f"Expected exit 0 when [[auth.telegram_bots]] key absent; "
        f"got {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert dest.exists()
    content = dest.read_text()
    assert "{{bot_secrets}}" not in content
    assert "Secret=lyra-bot-telegram-" not in content


def test_missing_marker(tmp_path: Path) -> None:
    """Template without {{bot_secrets}} marker → non-zero exit with error message.

    Spec trace: A6(c)
    Negative sentinel: if the missing-marker guard is deleted, the renderer would
    silently emit an unchanged template (no bot secrets injected), exit 0, and
    the operator would deploy a broken Quadlet without any signal.
    """
    # Arrange — template deliberately lacks the marker
    config = make_config(tmp_path, bots=["lyra"])
    tmpl = make_tmpl(tmp_path, with_marker=False)
    dest = tmp_path / "lyra-telegram.container"

    # Act
    result = _run_render(
        [
            "--platform",
            "telegram",
            "--config",
            str(config),
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


def test_missing_config(tmp_path: Path) -> None:
    """config.toml path that does not exist → non-zero exit with error message.

    Spec trace: A6(d)
    Negative sentinel: if the config-not-found guard is deleted, tomllib.load
    would raise an unhandled FileNotFoundError/OSError, producing an ugly
    traceback with exit 1 — but the spec requires a CLEAR error message.
    Removing the guard would still fail this test because we assert on message
    content, not just exit code.
    """
    # Arrange — point config at a path that does not exist
    config = tmp_path / "nonexistent-config.toml"
    tmpl = make_tmpl(tmp_path, with_marker=True)
    dest = tmp_path / "lyra-telegram.container"

    # Act
    result = _run_render(
        [
            "--platform",
            "telegram",
            "--config",
            str(config),
            "--tmpl",
            str(tmpl),
            "--dest",
            str(dest),
        ]
    )

    # Assert non-zero exit
    assert result.returncode != 0, (
        f"Expected non-zero exit for missing config; got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Error message must reference the missing path — clean message, no raw traceback.
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined, (
        f"Expected clean error message; got Python traceback.\nCombined: {combined!r}"
    )
    assert "config" in combined.lower() or "not found" in combined.lower(), (
        "Error message must reference the missing config path.\n"
        f"Combined output: {combined!r}"
    )


def make_config_with_webhook(
    tmp_path: Path, bots: list[dict], filename: str = "config.toml"
) -> Path:
    """Write a config.toml supporting arbitrary per-bot fields (e.g. webhook_enabled).

    bots: list of dicts with at least 'bot_id'; may include 'webhook_enabled'.
    """
    path = tmp_path / filename
    lines = ["[auth]\n"]
    for bot in bots:
        lines.append(f'[[auth.telegram_bots]]\nbot_id = "{bot["bot_id"]}"\n')
        if "webhook_enabled" in bot:
            val = "true" if bot["webhook_enabled"] else "false"
            lines.append(f"webhook_enabled = {val}\n")
        lines.append("\n")
    path.write_text("".join(lines))
    return path


def test_webhook_enabled_happy_path(tmp_path: Path) -> None:
    """Bot with webhook_enabled = true → emits both bot_token- and bot_webhook- lines.

    Spec trace: #1373 happy path webhook
    Negative sentinel: if the webhook branch is absent, only the token line appears
    and the bot_webhook- assertion fails.
    """
    config = make_config_with_webhook(
        tmp_path, bots=[{"bot_id": "lyra", "webhook_enabled": True}]
    )
    tmpl = make_tmpl(tmp_path, with_marker=True)
    dest = tmp_path / "lyra-telegram.container"

    result = _run_render(
        [
            "--platform",
            "telegram",
            "--config",
            str(config),
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
        "Secret=lyra-bot-telegram-lyra,"
        "type=mount,"
        "target=bot_token-lyra,"
        "mode=0400,"
        "uid=1500,"
        "gid=1500"
    )
    expected_webhook = (
        "Secret=lyra-bot-telegram-lyra-webhook,"
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


def test_webhook_disabled_no_webhook_line(tmp_path: Path) -> None:
    """Bot without webhook_enabled (default false) → only bot_token- line emitted.

    Spec trace: #1373 regression — default false
    Negative sentinel: if webhook_enabled defaults to True, this test fails on the
    no-webhook-line assertion.
    """
    # webhook_enabled absent → default false
    config = make_config(tmp_path, bots=["lyra"])
    tmpl = make_tmpl(tmp_path, with_marker=True)
    dest = tmp_path / "lyra-telegram.container"

    result = _run_render(
        [
            "--platform",
            "telegram",
            "--config",
            str(config),
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
        "bot_webhook- line must NOT appear when webhook_enabled is false/absent"
    )


def test_webhook_mixed_bots(tmp_path: Path) -> None:
    """Bot A (webhook_enabled=true) + Bot B (default false) → webhook only for A.

    Spec trace: #1373 mixed bots
    Negative sentinel: if webhook_enabled is ignored and always emitted, bot_webhook-b
    appears and the assertion fails.
    """
    config = make_config_with_webhook(
        tmp_path,
        bots=[
            {"bot_id": "a", "webhook_enabled": True},
            {"bot_id": "b"},
        ],
    )
    tmpl = make_tmpl(tmp_path, with_marker=True)
    dest = tmp_path / "lyra-telegram.container"

    result = _run_render(
        [
            "--platform",
            "telegram",
            "--config",
            str(config),
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


def test_webhook_string_value_does_not_emit(tmp_path: Path) -> None:
    """webhook_enabled = "yes" (TOML string, not bool) must NOT emit webhook line.

    Defensive: tomllib produces a str for quoted values; strict `is True` check
    in render_secrets() must reject non-bool to avoid silent misconfig.

    Spec trace: #1373 F5 follow-on
    Negative sentinel: if the check were truthy (e.g. `if b.get(...):`), a string
    "yes" would be truthy and incorrectly emit a bot_webhook- line.
    """
    # Write config.toml with webhook_enabled = "yes" (TOML string — NOT bool true)
    config = tmp_path / "config.toml"
    config.write_text(
        '[auth]\n[[auth.telegram_bots]]\nbot_id = "lyra"\nwebhook_enabled = "yes"\n'
    )
    tmpl = make_tmpl(tmp_path, with_marker=True)
    dest = tmp_path / "lyra-telegram.container"

    result = _run_render(
        [
            "--platform",
            "telegram",
            "--config",
            str(config),
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

    # String "yes" must NOT trigger webhook line emission
    assert "target=bot_webhook-" not in content, (
        'webhook_enabled = "yes" (string) must NOT emit a bot_webhook- Secret= line; '
        f"got:\n{content}"
    )
    # Token line must still be emitted normally
    assert "target=bot_token-lyra" in content, (
        "bot_token- line must still appear even when webhook_enabled is a string"
    )


def test_toml_syntax_error(tmp_path: Path) -> None:
    """config.toml with TOML syntax error → non-zero exit, parser error on output.

    Spec trace: A6(e)
    Negative sentinel: if the TOML parse error is silently swallowed (e.g.
    bare except: pass), the renderer would produce garbage output and exit 0.
    Removing the guard causes this test to fail on the returncode assertion.
    """
    # Arrange — write a deliberately malformed TOML file (unclosed section header)
    config = tmp_path / "bad-config.toml"
    config.write_text(
        '[auth]\n[[auth.telegram_bots\nbot_id = "lyra"\n'
    )  # missing closing ]
    tmpl = make_tmpl(tmp_path, with_marker=True)
    dest = tmp_path / "lyra-telegram.container"

    # Act
    result = _run_render(
        [
            "--platform",
            "telegram",
            "--config",
            str(config),
            "--tmpl",
            str(tmpl),
            "--dest",
            str(dest),
        ]
    )

    # Assert non-zero exit
    assert result.returncode != 0, (
        f"Expected non-zero exit for TOML syntax error; got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Error output must propagate the underlying TOML parser exception text — clean
    # message, no raw traceback.
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined, (
        f"Expected clean error message; got Python traceback.\nCombined: {combined!r}"
    )
    toml_error_signals = ("TOMLDecodeError", "Invalid", "Expected", "toml", "parse")
    assert any(sig.lower() in combined.lower() for sig in toml_error_signals), (
        f"Error output must include TOML parser exception text.\n"
        f"Combined output: {combined!r}"
    )
