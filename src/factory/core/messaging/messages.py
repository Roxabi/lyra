"""TOML-backed message template registry with i18n support."""

from __future__ import annotations

import logging
import tomllib
from collections import defaultdict
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_FALLBACKS: dict[str, str] = {
    "generic": "Something went wrong. Please try again.",
    "unavailable": "{bot_name} is currently unavailable. Please try again later.",
    "unknown_command": "Unknown command. Type /help for available commands.",
    "help_header": "Available commands:",
    "backpressure_ack": "Processing your request\u2026",
    "circuit_open_ack": "I'm temporarily overloaded, please try again in a moment.",
    "stream_placeholder": "\u2026",
    "stream_interrupted": " [response interrupted]",
    "timeout": "Your request timed out. Please try again.",
    "auth_required": "Your CLI session has expired. Please sign in again.",
    "rate_limit": "You've hit a usage limit. Please try again later.",
    "context_too_long": (
        "This conversation is too long for the model. Try /clear or start a new topic."
    ),
    "cancelled": "Request cancelled.",
    "stt_noise": "I couldn't make out your voice message, please try again.",
    "stt_unsupported": "Voice messages are not supported — STT is not configured.",
    "stt_unavailable": (
        "Voice messages are temporarily unavailable. "
        "Please try again later or send a text message."
    ),
    "stt_failed": "Sorry, I couldn't transcribe your voice message.",
    "stt_invalid": (
        "That voice message couldn't be processed. Please try again as text."
    ),
    "stt_too_long": (
        "That voice message is too long (transcript over the limit). "
        "Please send shorter clips (~10 min max) or split it into several messages."
    ),
    "audio_download_failed": "Couldn't retrieve your audio file. Please try again.",
    "model_fallback": (
        "⚠️ {requested_model} is unavailable — replying with {fallback_model}."
    ),
}


class MessageManager:
    """TOML-backed message template registry with i18n support.

    Resolution order for get(key, platform, **kwargs):
      1. adapters.{platform}.{lang}.{key}  — platform + language match
      2. adapters.{platform}.en.{key}      — platform match, EN fallback
      3. errors.{lang}.{key}               — global key, active language
      4. errors.en.{key}                   — global key, EN fallback
      5. _FALLBACKS[key]                   — hardcoded safety net (never raises)
    """

    def __init__(self, path: str | Path, language: str = "en") -> None:
        self.language = language
        try:
            with open(path, "rb") as f:
                self._templates: dict[str, Any] = tomllib.load(f)
        except (tomllib.TOMLDecodeError, OSError):
            log.warning("Failed to load messages.toml at %s — using fallbacks", path)
            self._templates: dict[str, Any] = {}

    def get(self, key: str, platform: str | None = None, **kwargs: str) -> str:
        """Return resolved template string. Never raises."""
        fmt = dict(kwargs)
        fmt.setdefault("bot_name", "factory")
        try:
            raw = self._resolve(key, platform)
            if not raw:
                raw = self._resolve_notification(key)
            return raw.format_map(fmt)
        except (KeyError, ValueError) as exc:
            log.debug(
                "MessageManager.get(%r, platform=%r) fell back: %s", key, platform, exc
            )
            return self._format_fallback(key, fmt)

    def _format_fallback(self, key: str, fmt: dict[str, str]) -> str:
        raw = _FALLBACKS.get(key, "")
        if not raw:
            return ""
        try:
            return raw.format_map(fmt)
        except (KeyError, ValueError):
            safe = defaultdict(str, fmt)
            try:
                return raw.format_map(safe)
            except ValueError:
                return raw

    def _resolve_notification(self, key: str) -> str:
        lang = self.language
        notifications = self._templates.get("notifications", {})
        if key in notifications.get(lang, {}):
            return notifications[lang][key]
        if key in notifications.get("en", {}):
            return notifications["en"][key]
        return _FALLBACKS.get(key, "")

    def _resolve(self, key: str, platform: str | None) -> str:
        lang = self.language
        adapters = self._templates.get("adapters", {})
        errors = self._templates.get("errors", {})
        if platform:
            plat = adapters.get(platform, {})
            if key in plat.get(lang, {}):
                return plat[lang][key]
            if key in plat.get("en", {}):
                return plat["en"][key]
        if key in errors.get(lang, {}):
            return errors[lang][key]
        if key in errors.get("en", {}):
            return errors["en"][key]
        return _FALLBACKS.get(key, "")
