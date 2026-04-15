"""Tests for NATS queue group name helpers."""

from __future__ import annotations

from lyra.nats.queue_groups import (
    HUB_INBOUND,
    STT_WORKERS,
    TTS_WORKERS,
    adapter_outbound,
)


class TestAdapterOutbound:
    def test_formats_subject_for_telegram(self) -> None:
        subject = adapter_outbound("telegram", "bot42")
        assert subject == "adapter-outbound-telegram-bot42"

    def test_formats_subject_for_discord(self) -> None:
        subject = adapter_outbound("discord", "main")
        assert subject == "adapter-outbound-discord-main"

    def test_accepts_enum_value_via_str(self) -> None:
        # Regression: Platform enum is str-subclass; callers pass .value explicitly.
        from lyra.core.message import Platform

        subject = adapter_outbound(Platform.TELEGRAM.value, "abc")
        assert subject == "adapter-outbound-telegram-abc"


class TestConstants:
    def test_hub_inbound_name_is_stable(self) -> None:
        assert HUB_INBOUND == "hub-inbound"

    def test_worker_queue_names_are_stable(self) -> None:
        assert TTS_WORKERS == "tts-workers"
        assert STT_WORKERS == "stt-workers"
