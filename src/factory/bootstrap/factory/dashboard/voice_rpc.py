"""Hub-side voice capabilities RPC for factory-dashboard BFF."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from roxabi_contracts.dashboard import (
    DashboardVoiceCapabilitiesResponse,
    VoiceEngineInfo,
    VoiceSampleInfo,
    VoiceSttCapabilities,
    VoiceTtsCapabilities,
)

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.core.hub import Hub


async def handle_voice_capabilities(
    hub: Hub, nc: NATS, _payload: dict[str, Any]
) -> dict[str, Any]:
    _ = hub
    from factory.nats.voice.voice_lifecycle_client import VoiceLifecycleClient

    caps = await VoiceLifecycleClient(nc).capabilities()
    tts_raw = caps.get("tts")
    stt_raw = caps.get("stt")
    tts = None
    stt = None
    if isinstance(tts_raw, dict):
        tts = VoiceTtsCapabilities(
            engines=[
                VoiceEngineInfo.model_validate(e) for e in tts_raw.get("engines", [])
            ],
            samples=[
                VoiceSampleInfo.model_validate(s) for s in tts_raw.get("samples", [])
            ],
            max_cached_engines=int(tts_raw.get("max_cached_engines") or 1),
            default_engine=tts_raw.get("default_engine"),
            catalog_revision=tts_raw.get("catalog_revision"),
        )
    if isinstance(stt_raw, dict):
        stt = VoiceSttCapabilities(
            models=list(stt_raw.get("models") or []),
            default_model=stt_raw.get("default_model"),
        )
    if tts is None and stt is None:
        return DashboardVoiceCapabilitiesResponse(
            error="voice_workers_unreachable",
        ).model_dump()
    return DashboardVoiceCapabilitiesResponse(tts=tts, stt=stt).model_dump()