"""Telegram bot management CLI — thin shim over the parameterized platform module."""

from __future__ import annotations

from factory.agent_cmd.platforms.platform import make_platform_app

telegram_app = make_platform_app("telegram")
