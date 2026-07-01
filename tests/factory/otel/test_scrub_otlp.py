"""Tests for OTLP ingest attribute scrubbing."""

from __future__ import annotations

from factory.otel.scrub_otlp import scrub_otlp_dict

_JOB_ATTR = {"key": "roxabi.job_id", "value": {"stringValue": "a"}}
_GEN_AI_ATTR = {
    "key": "gen_ai.request.model",
    "value": {"stringValue": "gpt-4"},
}
_PROMPT_ATTR = {"key": "prompt", "value": {"stringValue": "secret"}}


def test_scrub_otlp_drops_gen_ai_and_forbidden() -> None:
    payload = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "attributes": [
                                    _JOB_ATTR,
                                    _GEN_AI_ATTR,
                                    _PROMPT_ATTR,
                                ]
                            }
                        ]
                    }
                ]
            }
        ]
    }
    dropped = scrub_otlp_dict(payload)
    attrs = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]
    assert dropped == 2
    assert attrs == [_JOB_ATTR]