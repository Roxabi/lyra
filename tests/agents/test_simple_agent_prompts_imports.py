"""Regression guard for #1225 — STTNoiseError shadow class.

The local `STTNoiseError` class previously defined in
`factory.agents.simple_agent_prompts` shadowed the canonical class in
`factory.core.ports.stt`. If a future change reintroduces a local definition,
callers that `except STTNoiseError` from the port would silently stop
catching it. This test fails immediately on re-shadow.
"""

from __future__ import annotations


def test_stt_noise_error_is_canonical() -> None:
    from factory.agents.simple_agent import STTNoiseError as AgentSTT
    from factory.agents.simple_agent_prompts import STTNoiseError as PromptSTT
    from factory.core.ports.stt import STTNoiseError as PortSTT

    assert PromptSTT is PortSTT
    assert AgentSTT is PortSTT
