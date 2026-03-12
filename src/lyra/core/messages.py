"""TOML-backed message template registry with i18n support."""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_FALLBACKS: dict[str, str] = {
    "generic": "Something went wrong. Please try again.",
    "unavailable": "Lyra is currently unavailable. Please try again later.",
    "unknown_command": "Unknown command. Type /help for available commands.",
    "help_header": "Available commands:",
    "backpressure_ack": "Processing your request\u2026",
    "stream_placeholder": "\u2026",
    "stream_interrupted": " [response interrupted]",
    "timeout": "Your request timed out. Please try again.",
    "cancelled": "Request cancelled.",
    "stt_noise": "I couldn't make out your voice message, please try again.",
    "stt_unsupported": "Voice messages are not supported — STT is not configured.",
    "stt_failed": "Sorry, I couldn't transcribe your voice message.",
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
        except Exception:
            log.warning("Failed to load messages.toml at %s — using fallbacks", path)
            self._templates: dict[str, Any] = {}

    def get(self, key: str, platform: str | None = None, **kwargs: str) -> str:
        """Return resolved template string. Never raises."""
        try:
            raw = self._resolve(key, platform)
            return raw.format_map(kwargs)
        except Exception as exc:
            log.debug(
                "MessageManager.get(%r, platform=%r) fell back: %s", key, platform, exc
            )
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
