"""Tests for production guard on LYRA_RUN_SECRETS_DIR (issue #1304).

Verifies that load_bot_token ignores the env override when running inside a
container (/run/.containerenv exists) or when LYRA_ENV=prod, falling back to
/run/secrets as a defense-in-depth measure.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from lyra.bootstrap.credentials import _is_prod_env, load_bot_token
from lyra.errors import MissingCredentialsError


class TestIsProdEnv:
    """Unit tests for _is_prod_env helper."""

    def test_true_when_lyra_env_is_prod(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("LYRA_ENV", "prod")
        assert _is_prod_env() is True

    def test_true_when_containerenv_exists(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.delenv("LYRA_ENV", raising=False)
        fake_containerenv = tmp_path / ".containerenv"
        fake_containerenv.write_text("")
        with patch(
            "lyra.bootstrap.credentials.Path",
            side_effect=lambda p: (
                fake_containerenv if p == "/run/.containerenv" else Path(p)
            ),
        ):
            assert _is_prod_env() is True

    def test_false_when_neither(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.delenv("LYRA_ENV", raising=False)
        missing = tmp_path / "missing"
        with patch(
            "lyra.bootstrap.credentials.Path",
            side_effect=lambda p: missing if p == "/run/.containerenv" else Path(p),
        ):
            assert _is_prod_env() is False


class TestLoadBotTokenProdGuard:
    """Unit tests for the production guard in load_bot_token."""

    def test_ignores_override_in_container(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When /run/.containerenv exists, LYRA_RUN_SECRETS_DIR is ignored."""
        fake_containerenv = tmp_path / ".containerenv"
        fake_containerenv.write_text("")

        fake_secrets = tmp_path / "fake-secrets"
        fake_secrets.mkdir()
        (fake_secrets / "bot_token-mybot").write_text("OVERRIDE_TOKEN")

        prod_secrets = tmp_path / "prod-secrets"
        prod_secrets.mkdir()
        (prod_secrets / "bot_token-mybot").write_text("PROD_TOKEN")

        monkeypatch.setenv("LYRA_RUN_SECRETS_DIR", str(fake_secrets))
        monkeypatch.delenv("LYRA_ENV", raising=False)

        with patch("lyra.bootstrap.credentials._PROD_SECRETS_DIR", prod_secrets):
            with patch(
                "lyra.bootstrap.credentials.Path",
                side_effect=lambda p: (
                    fake_containerenv if p == "/run/.containerenv" else Path(p)
                ),
            ):
                token, webhook = load_bot_token("telegram", "mybot")

        assert token == "PROD_TOKEN"
        assert webhook is None

    def test_ignores_override_when_lyra_env_prod(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When LYRA_ENV=prod, LYRA_RUN_SECRETS_DIR is ignored."""
        fake_secrets = tmp_path / "fake-secrets"
        fake_secrets.mkdir()
        (fake_secrets / "bot_token-mybot").write_text("OVERRIDE_TOKEN")

        prod_secrets = tmp_path / "prod-secrets"
        prod_secrets.mkdir()
        (prod_secrets / "bot_token-mybot").write_text("PROD_TOKEN")

        monkeypatch.setenv("LYRA_RUN_SECRETS_DIR", str(fake_secrets))
        monkeypatch.setenv("LYRA_ENV", "prod")

        with patch("lyra.bootstrap.credentials._PROD_SECRETS_DIR", prod_secrets):
            token, webhook = load_bot_token("telegram", "mybot")

        assert token == "PROD_TOKEN"
        assert webhook is None

    def test_allows_override_in_dev(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """In development (no container, no LYRA_ENV=prod), override is honored."""
        dev_secrets = tmp_path / "dev-secrets"
        dev_secrets.mkdir()
        (dev_secrets / "bot_token-mybot").write_text("DEV_TOKEN")

        monkeypatch.setenv("LYRA_RUN_SECRETS_DIR", str(dev_secrets))
        monkeypatch.delenv("LYRA_ENV", raising=False)

        missing = tmp_path / "missing"
        with patch(
            "lyra.bootstrap.credentials.Path",
            side_effect=lambda p: missing if p == "/run/.containerenv" else Path(p),
        ):
            token, webhook = load_bot_token("telegram", "mybot")

        assert token == "DEV_TOKEN"
        assert webhook is None

    def test_reads_webhook_from_prod_when_override_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Webhook is also read from /run/secrets when override is ignored."""
        fake_secrets = tmp_path / "fake-secrets"
        fake_secrets.mkdir()

        prod_secrets = tmp_path / "prod-secrets"
        prod_secrets.mkdir()
        (prod_secrets / "bot_token-mybot").write_text("PROD_TOKEN")
        (prod_secrets / "bot_webhook-mybot").write_text("PROD_WEBHOOK")

        monkeypatch.setenv("LYRA_RUN_SECRETS_DIR", str(fake_secrets))
        monkeypatch.setenv("LYRA_ENV", "prod")

        with patch("lyra.bootstrap.credentials._PROD_SECRETS_DIR", prod_secrets):
            token, webhook = load_bot_token("telegram", "mybot")

        assert token == "PROD_TOKEN"
        assert webhook == "PROD_WEBHOOK"

    def test_missing_token_raises_with_prod_path_when_override_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Error message references /run/secrets when override is ignored in prod."""
        fake_secrets = tmp_path / "fake-secrets"
        fake_secrets.mkdir()
        (fake_secrets / "bot_token-mybot").write_text("OVERRIDE_TOKEN")

        prod_secrets = tmp_path / "prod-secrets"
        prod_secrets.mkdir()
        # No token file in prod_secrets

        monkeypatch.setenv("LYRA_RUN_SECRETS_DIR", str(fake_secrets))
        monkeypatch.setenv("LYRA_ENV", "prod")

        with patch("lyra.bootstrap.credentials._PROD_SECRETS_DIR", prod_secrets):
            with pytest.raises(MissingCredentialsError) as exc_info:
                load_bot_token("telegram", "mybot")

        assert str(prod_secrets / "bot_token-mybot") in str(exc_info.value)
