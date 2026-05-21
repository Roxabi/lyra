"""RED tests for `lyra bot secret` subcommands (issue #1057).

These tests MUST FAIL until T2 implements the `secret` sub-app in
`src/lyra/cli_bot.py`. They exercise:
  - lyra bot secret install <platform> <bot_id>
      [--from-env VAR] [--webhook-from-env VAR]
  - lyra bot secret rm <platform> <bot_id>
  - lyra bot secret list
  - lyra bot secret migrate --vault <dir>

Mock strategy: `subprocess.run` is patched so that no real podman process is
spawned. The root `lyra_app` from `lyra.cli` is invoked through `CliRunner`
to exercise the full Typer command tree (bot → secret).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from lyra.cli import (
    lyra_app as app,  # type: ignore[attr-defined]  # secret sub-app not yet
)

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proc(returncode: int = 0) -> MagicMock:
    """Return a mock CompletedProcess with the given returncode."""
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = returncode
    proc.stdout = b""
    proc.stderr = b""
    return proc


def _assert_exit0(result: object, label: str = "") -> None:
    """Assert exit_code == 0 with a readable failure message."""
    from typing import cast

    from click.testing import Result  # local import for type only

    r = cast(Result, result)
    prefix = f"[{label}] " if label else ""
    assert r.exit_code == 0, (
        f"{prefix}Expected exit 0, got {r.exit_code}:\n{r.output}"
    )


# ---------------------------------------------------------------------------
# TestSecretInstall
# ---------------------------------------------------------------------------


class TestSecretInstall:
    """lyra bot secret install — creates podman secrets from env vars."""

    def test_install_creates_podman_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """install --from-env calls podman secret create --replace with stdin."""
        monkeypatch.setenv("TKN", "ABC")

        mock_run = MagicMock(return_value=_make_proc(0))
        with patch("subprocess.run", mock_run):
            result = runner.invoke(
                app,
                [
                    "bot",
                    "secret",
                    "install",
                    "telegram",
                    "demo",
                    "--from-env",
                    "TKN",
                ],
            )

        _assert_exit0(result, label="install creates secret")

        # subprocess.run must have been called with the podman create command
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert cmd[0] == "podman"
        assert "secret" in cmd
        assert "create" in cmd
        assert "--replace" in cmd
        assert "lyra-bot-telegram-demo" in cmd

        # Token bytes must have been piped via stdin
        kwargs = call_args[1]
        assert kwargs.get("input") == b"ABC"

    @pytest.mark.parametrize(
        "bad_id",
        [
            "bot/1",
            "bot id",
            "../etc",
            "",
        ],
    )
    def test_install_rejects_unsafe_bot_id(
        self, bad_id: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """bot_id not matching ^[A-Za-z0-9_-]+$ exits 2 with a validation msg.

        Negative-test contract: once the `secret` sub-app exists, deleting the
        bot_id regex guard must make this test fail (the command would succeed
        instead of exiting 2 with a validation message).  We therefore assert
        on a bot_id-specific error keyword so the test is NOT tautologically
        satisfied by "No such command 'secret'".
        """
        monkeypatch.setenv("TKN", "ABC")

        mock_run = MagicMock(return_value=_make_proc(0))
        with patch("subprocess.run", mock_run):
            result = runner.invoke(
                app,
                [
                    "bot",
                    "secret",
                    "install",
                    "telegram",
                    bad_id,
                    "--from-env",
                    "TKN",
                ],
            )

        assert result.exit_code == 2, (
            f"Expected exit 2 for bot_id={bad_id!r}, "
            f"got {result.exit_code}:\n{result.output}"
        )
        combined = (result.output or "") + (result.stderr or "")
        # Must mention bot_id validation — NOT just "No such command 'secret'"
        valid_keywords = (
            "bot_id",
            "bot-id",
            "invalid",
            "Invalid",
            "alphanumeric",
        )
        assert any(kw in combined for kw in valid_keywords), (
            f"Expected a bot_id validation message for bot_id={bad_id!r}, "
            f"got:\n{combined}"
        )
        # No podman call should have been made
        mock_run.assert_not_called()

    def test_install_with_webhook_creates_two_secrets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """install --from-env + --webhook-from-env creates both podman secrets."""
        monkeypatch.setenv("TKN", "TOKEN_VAL")
        monkeypatch.setenv("WHK", "WEBHOOK_VAL")

        mock_run = MagicMock(return_value=_make_proc(0))
        with patch("subprocess.run", mock_run):
            result = runner.invoke(
                app,
                [
                    "bot",
                    "secret",
                    "install",
                    "telegram",
                    "demo",
                    "--from-env",
                    "TKN",
                    "--webhook-from-env",
                    "WHK",
                ],
            )

        _assert_exit0(result, label="install webhook creates two secrets")

        # Expect exactly two subprocess.run calls
        assert mock_run.call_count == 2, (
            f"Expected 2 podman calls, got {mock_run.call_count}"
        )

        all_calls = mock_run.call_args_list
        cmds = [c[0][0] for c in all_calls]

        # last positional arg is the secret name
        secret_names = [cmd[-1] for cmd in cmds]
        assert "lyra-bot-telegram-demo" in secret_names
        assert "lyra-bot-telegram-demo-webhook" in secret_names

        for c in all_calls:
            cmd = c[0][0]
            assert "--replace" in cmd


# ---------------------------------------------------------------------------
# TestSecretRm
# ---------------------------------------------------------------------------


class TestSecretRm:
    """lyra bot secret rm — removes token and webhook secrets."""

    def test_rm_removes_both_token_and_webhook(self) -> None:
        """rm calls podman secret rm for both the token and webhook names."""
        mock_run = MagicMock(return_value=_make_proc(0))
        with patch("subprocess.run", mock_run):
            result = runner.invoke(
                app,
                ["bot", "secret", "rm", "telegram", "demo"],
            )

        _assert_exit0(result, label="rm both secrets")

        # Both names must appear in podman calls
        all_calls = mock_run.call_args_list
        all_args: list[str] = []
        for c in all_calls:
            all_args.extend(c[0][0])

        assert "lyra-bot-telegram-demo" in all_args
        assert "lyra-bot-telegram-demo-webhook" in all_args

    def test_rm_tolerates_missing_webhook_secret(self) -> None:
        """rm exits 0 even when the webhook secret is absent (podman error)."""

        call_count = 0

        def _side_effect(cmd: list[str], **_: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            # Simulate webhook secret missing
            if "lyra-bot-telegram-demo-webhook" in cmd:
                return _make_proc(125)  # podman: secret not found
            return _make_proc(0)

        with patch("subprocess.run", side_effect=_side_effect):
            result = runner.invoke(
                app,
                ["bot", "secret", "rm", "telegram", "demo"],
            )

        _assert_exit0(result, label="rm tolerates missing webhook")


# ---------------------------------------------------------------------------
# TestSecretList
# ---------------------------------------------------------------------------


class TestSecretList:
    """lyra bot secret list — filters by lyra-bot- prefix and echoes JSON."""

    def test_list_filters_by_lyra_bot_prefix(self) -> None:
        """list calls podman secret ls with filter + json format, echoes output."""
        fake_json = b'[{"Name":"lyra-bot-telegram-demo","ID":"abc123"}]'
        mock_proc = _make_proc(0)
        mock_proc.stdout = fake_json
        mock_run = MagicMock(return_value=mock_proc)

        with patch("subprocess.run", mock_run):
            result = runner.invoke(app, ["bot", "secret", "list"])

        _assert_exit0(result, label="list filters by prefix")

        # Verify the subprocess call
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "podman" in cmd
        assert "secret" in cmd
        assert "ls" in cmd
        assert "--filter" in cmd
        filter_idx = cmd.index("--filter")
        assert cmd[filter_idx + 1] == "name=lyra-bot-"
        assert "--format" in cmd
        fmt_idx = cmd.index("--format")
        assert cmd[fmt_idx + 1] == "json"

        # Output must include the mocked JSON payload
        assert "lyra-bot-telegram-demo" in result.output


# ---------------------------------------------------------------------------
# TestSecretListRoundTrip
# ---------------------------------------------------------------------------


class TestSecretListRoundTrip:
    """T3 — list reflects what install stored (semantic round-trip)."""

    def test_list_returns_what_was_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """list output must contain both secrets that install created.

        Negative-test contract: if list_ no longer calls podman secret ls with
        the captured names, the assertion on result.output will fail.
        """
        # Arrange
        monkeypatch.setenv("TKN1", "A")
        monkeypatch.setenv("TKN2", "B")

        install_proc = _make_proc(0)
        mock_run = MagicMock(return_value=install_proc)

        # Act — install bot1
        with patch("subprocess.run", mock_run):
            r1 = runner.invoke(
                app,
                ["bot", "secret", "install", "telegram", "bot1", "--from-env", "TKN1"],
            )
        _assert_exit0(r1, label="install bot1")
        install_calls = mock_run.call_args_list
        cmds1 = [c[0][0] for c in install_calls]
        assert any("lyra-bot-telegram-bot1" in cmd for cmd in cmds1)

        # Act — install bot2
        mock_run.reset_mock()
        with patch("subprocess.run", mock_run):
            r2 = runner.invoke(
                app,
                ["bot", "secret", "install", "telegram", "bot2", "--from-env", "TKN2"],
            )
        _assert_exit0(r2, label="install bot2")
        install_calls2 = mock_run.call_args_list
        cmds2 = [c[0][0] for c in install_calls2]
        assert any("lyra-bot-telegram-bot2" in cmd for cmd in cmds2)

        # Reconfigure mock: podman secret ls returns both names as JSON array
        fake_json = (
            b'[{"Name":"lyra-bot-telegram-bot1","ID":"aaa"},'
            b'{"Name":"lyra-bot-telegram-bot2","ID":"bbb"}]'
        )
        ls_proc = _make_proc(0)
        ls_proc.stdout = fake_json
        mock_run.reset_mock()
        mock_run.return_value = ls_proc

        # Act — list
        with patch("subprocess.run", mock_run):
            r3 = runner.invoke(app, ["bot", "secret", "list"])
        _assert_exit0(r3, label="list")

        # Assert — both names surface in the output
        assert "lyra-bot-telegram-bot1" in r3.output, (
            f"Expected lyra-bot-telegram-bot1 in list output:\n{r3.output}"
        )
        assert "lyra-bot-telegram-bot2" in r3.output, (
            f"Expected lyra-bot-telegram-bot2 in list output:\n{r3.output}"
        )


# ---------------------------------------------------------------------------
# TestE2EV1RedGate
# ---------------------------------------------------------------------------


class TestE2EV1RedGate:
    """T7 RED-GATE V1 — install path + Makefile recipe contract.

    Full make invocation is reserved for the manual M₂ smoke test.
    This test asserts the Makefile recipe shape is correct so that the
    install → render contract is verifiable without spawning make.
    """

    def test_e2e_install_then_list_then_render(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """install two secrets, verify list, then assert Makefile recipe format.

        Negative-test contract:
        - Delete the install path → install_calls assertions fail.
        - Remove mode=0400,uid=1500,gid=1500 from Makefile → regex fails.
        - Change target= naming convention → bot_token/bot_webhook assertions fail.
        """
        import re

        # Arrange
        monkeypatch.setenv("TKN", "token-value")
        monkeypatch.setenv("WHK", "webhook-value")

        create_proc = _make_proc(0)
        mock_run = MagicMock(return_value=create_proc)

        # Act — install with both token and webhook
        with patch("subprocess.run", mock_run):
            r_install = runner.invoke(
                app,
                [
                    "bot",
                    "secret",
                    "install",
                    "telegram",
                    "mybot",
                    "--from-env",
                    "TKN",
                    "--webhook-from-env",
                    "WHK",
                ],
            )
        _assert_exit0(r_install, label="install mybot")

        # Assert — exactly 2 podman secret create calls
        assert mock_run.call_count == 2, (
            f"Expected 2 podman create calls, got {mock_run.call_count}"
        )
        created_names = [c[0][0][-1] for c in mock_run.call_args_list]
        assert "lyra-bot-telegram-mybot" in created_names
        assert "lyra-bot-telegram-mybot-webhook" in created_names

        # Reconfigure mock: podman secret ls uses plain-text {{.Name}} format
        ls_proc = _make_proc(0)
        ls_proc.stdout = b"lyra-bot-telegram-mybot\nlyra-bot-telegram-mybot-webhook\n"
        mock_run.reset_mock()
        mock_run.return_value = ls_proc

        # Act — list (json format path)
        ls_json_proc = _make_proc(0)
        ls_json_proc.stdout = (
            b'[{"Name":"lyra-bot-telegram-mybot","ID":"x1"},'
            b'{"Name":"lyra-bot-telegram-mybot-webhook","ID":"x2"}]'
        )
        mock_run.return_value = ls_json_proc
        with patch("subprocess.run", mock_run):
            r_list = runner.invoke(app, ["bot", "secret", "list"])
        _assert_exit0(r_list, label="list after install")
        assert "lyra-bot-telegram-mybot" in r_list.output

        # Assert Makefile recipe shape — no subprocess call to make needed.
        # Asserts the Makefile recipe shape; full make invocation is reserved
        # for the manual M₂ smoke test.
        makefile_path = (
            Path(__file__).parent.parent.parent / "Makefile"
        )
        makefile_contents = makefile_path.read_text()

        # Secret= line must carry the correct mount options
        secret_format_re = re.compile(
            r"Secret=\$\$s,type=mount,target=\$\$target,mode=0400,uid=1500,gid=1500"
        )
        assert secret_format_re.search(makefile_contents), (
            "Makefile Secret= line missing mode=0400,uid=1500,gid=1500 mount options"
        )

        # target= for token must be bot_token-<bot>
        assert re.search(r"target=bot_token-\$\$bot", makefile_contents), (
            "Makefile missing target=bot_token-$$bot assignment"
        )

        # target= for webhook must be bot_webhook-<bot>
        assert re.search(r"target=bot_webhook-\$\$bot", makefile_contents), (
            "Makefile missing target=bot_webhook-$$bot assignment"
        )
