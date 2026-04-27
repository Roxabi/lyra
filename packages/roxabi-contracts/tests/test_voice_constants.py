"""Smoke tests for TTS field constants in roxabi_contracts.voice.constants."""

from __future__ import annotations

from roxabi_contracts.voice.constants import AGENT_TTS_FIELDS, TTS_CONFIG_FIELDS


class TestShape:
    def test_config_fields_is_tuple_of_strings(self) -> None:
        assert isinstance(TTS_CONFIG_FIELDS, tuple)
        assert TTS_CONFIG_FIELDS
        assert all(isinstance(f, str) for f in TTS_CONFIG_FIELDS)

    def test_agent_fields_is_tuple_of_strings(self) -> None:
        assert isinstance(AGENT_TTS_FIELDS, tuple)
        assert AGENT_TTS_FIELDS
        assert all(isinstance(f, str) for f in AGENT_TTS_FIELDS)

    def test_no_duplicate_fields_within_each_tuple(self) -> None:
        assert len(TTS_CONFIG_FIELDS) == len(set(TTS_CONFIG_FIELDS))
        assert len(AGENT_TTS_FIELDS) == len(set(AGENT_TTS_FIELDS))


class TestBoundaryContract:
    def test_engine_is_in_both_tuples(self) -> None:
        # engine is the routing key between hub and adapter — must exist
        # on both sides of the boundary.
        assert "engine" in TTS_CONFIG_FIELDS
        assert "engine" in AGENT_TTS_FIELDS

    def test_hub_subset_is_carried_in_agent(self) -> None:
        # Every field the hub sends must be receivable on the adapter side.
        # (voice and language are passed as explicit kwargs, hence absent
        # from the hub tuple but present on the adapter side.)
        hub_shared = set(TTS_CONFIG_FIELDS)
        agent_shared = set(AGENT_TTS_FIELDS)
        assert hub_shared.issubset(agent_shared)

    def test_agent_only_fields_are_kwargs_or_defaults(self) -> None:
        # Fields on the adapter that are not in the hub tuple must be ones
        # the adapter reads from defaults or explicit kwargs — document the
        # known set so accidental additions fail this test and force a review.
        agent_only = set(AGENT_TTS_FIELDS) - set(TTS_CONFIG_FIELDS)
        assert agent_only == {"voice", "language", "default_language", "languages"}
