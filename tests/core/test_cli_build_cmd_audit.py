"""Audit logging tests for build_cmd() skip_permissions bypass."""

from __future__ import annotations

import logging

import pytest

from lyra.core.agent.agent_config import ModelConfig
from lyra.core.cli.cli_protocol import build_cmd


class TestBuildCmdSkipPermissionsAudit:
    """build_cmd() must emit a WARNING when skip_permissions is True."""

    def test_warning_logged_when_skip_permissions_true(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """WARNING emitted at lyra.core.cli.cli_protocol when skip_permissions=True."""
        model_config = ModelConfig(skip_permissions=True)

        with caplog.at_level(logging.WARNING, logger="lyra.core.cli.cli_protocol"):
            cmd, prompt_file = build_cmd(model_config)

        assert prompt_file is None
        assert "--dangerously-skip-permissions" in cmd
        assert any(
            "SECURITY" in record.message
            and "dangerously-skip-permissions" in record.message
            for record in caplog.records
        ), "Expected SECURITY warning for skip_permissions bypass"
        assert any(
            record.levelno == logging.WARNING for record in caplog.records
        )

    def test_no_warning_when_skip_permissions_false(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No WARNING is emitted when skip_permissions=False."""
        model_config = ModelConfig(skip_permissions=False)

        with caplog.at_level(logging.WARNING, logger="lyra.core.cli.cli_protocol"):
            cmd, prompt_file = build_cmd(model_config)

        assert prompt_file is None
        assert "--dangerously-skip-permissions" not in cmd
        assert not any(
            "dangerously-skip-permissions" in record.message
            for record in caplog.records
        )

    def test_warning_exact_message(self, caplog: pytest.LogCaptureFixture) -> None:
        """Warning message matches expected audit string exactly."""
        model_config = ModelConfig(skip_permissions=True)

        with caplog.at_level(logging.WARNING, logger="lyra.core.cli.cli_protocol"):
            build_cmd(model_config)

        messages = [r.message for r in caplog.records]
        assert (
            "SECURITY: --dangerously-skip-permissions enabled for CLI subprocess"
            in messages
        )
