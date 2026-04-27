"""Boundary tests for sanitize_platform_meta.

Verifies the allowlist-based platform_meta sanitizer used at the NATS trust
boundary. The sanitizer strips unknown keys, underscore-prefixed keys
(internal-only), and non-scalar values — preventing session hijacking via
crafted NATS messages.
"""

from __future__ import annotations

from roxabi_nats._sanitize import (
    MAX_META_VALUE_LEN,
    PLATFORM_META_ALLOWLIST,
    sanitize_platform_meta,
)


class TestAllowlist:
    def test_allowlisted_keys_pass_through(self) -> None:
        meta = {
            "guild_id": "123",
            "channel_id": "456",
            "message_id": "789",
            "thread_id": "tid",
            "channel_type": "text",
            "chat_id": 42,
            "topic_id": 7,
            "is_group": True,
            "thread_session_id": "session-1",
        }
        result = sanitize_platform_meta(meta)
        assert result == meta

    def test_unknown_keys_stripped(self) -> None:
        meta = {
            "guild_id": "123",
            "attacker_injected": "payload",
            "session_token": "should-not-survive",
        }
        result = sanitize_platform_meta(meta)
        assert result == {"guild_id": "123"}

    def test_underscore_prefixed_keys_stripped_even_when_on_allowlist(
        self,
    ) -> None:
        # Even if a key is on the allowlist, a leading underscore variant
        # (e.g. _session_update_fn injection attempt) is stripped.
        meta = {"_guild_id": "attacker", "guild_id": "legit"}
        result = sanitize_platform_meta(meta)
        assert result == {"guild_id": "legit"}


class TestScalarValidation:
    def test_dict_value_dropped(self) -> None:
        meta = {"guild_id": {"nested": "payload"}}
        assert sanitize_platform_meta(meta) == {}

    def test_list_value_dropped(self) -> None:
        meta = {"guild_id": ["a", "b", "c"]}
        assert sanitize_platform_meta(meta) == {}

    def test_none_value_dropped(self) -> None:
        meta = {"guild_id": None}
        assert sanitize_platform_meta(meta) == {}

    def test_float_value_dropped(self) -> None:
        # Only str/int/bool pass; float is not in the allowed scalar set.
        meta = {"guild_id": 3.14}
        assert sanitize_platform_meta(meta) == {}

    def test_scalars_preserved(self) -> None:
        meta = {"guild_id": "abc", "chat_id": 42, "is_group": False}
        assert sanitize_platform_meta(meta) == meta


class TestLengthCap:
    def test_string_under_cap_untouched(self) -> None:
        val = "x" * (MAX_META_VALUE_LEN - 1)
        assert sanitize_platform_meta({"guild_id": val}) == {"guild_id": val}

    def test_string_at_cap_untouched(self) -> None:
        val = "x" * MAX_META_VALUE_LEN
        assert sanitize_platform_meta({"guild_id": val}) == {"guild_id": val}

    def test_string_over_cap_truncated(self) -> None:
        val = "x" * (MAX_META_VALUE_LEN + 500)
        result = sanitize_platform_meta({"guild_id": val})
        assert len(result["guild_id"]) == MAX_META_VALUE_LEN

    def test_int_and_bool_not_length_capped(self) -> None:
        meta = {"chat_id": 10**50, "is_group": True}
        assert sanitize_platform_meta(meta) == meta


class TestEmptyAndAllowlistShape:
    def test_empty_dict_returns_empty_dict(self) -> None:
        assert sanitize_platform_meta({}) == {}

    def test_allowlist_is_immutable(self) -> None:
        assert isinstance(PLATFORM_META_ALLOWLIST, frozenset)


class TestInjectableAllowlist:
    def test_custom_allowlist_used_when_provided(self) -> None:
        # "custom_only" is NOT on PLATFORM_META_ALLOWLIST — proves the custom set is
        # active. "guild_id" IS on PLATFORM_META_ALLOWLIST — proves the default is not.
        custom = frozenset({"custom_only", "other_field"})
        meta = {"custom_only": "keep", "guild_id": "default-only", "other_field": 1}
        result = sanitize_platform_meta(meta, allowlist=custom)
        assert result == {"custom_only": "keep", "other_field": 1}
        assert "guild_id" not in result

    def test_default_allowlist_used_when_none(self) -> None:
        meta = {"guild_id": "123", "unknown": "x"}
        assert sanitize_platform_meta(meta, allowlist=None) == {"guild_id": "123"}

    def test_empty_custom_allowlist_strips_everything(self) -> None:
        meta = {"guild_id": "123", "chat_id": 42}
        assert sanitize_platform_meta(meta, allowlist=frozenset()) == {}

    def test_underscore_keys_still_stripped_with_custom_allowlist(self) -> None:
        # "_custom_field" IS in custom — the only operative filter is the underscore
        # guard; if that guard were removed the key would pass through.
        custom = frozenset({"custom_field", "_custom_field"})
        meta = {"custom_field": "ok", "_custom_field": "injected"}
        result = sanitize_platform_meta(meta, allowlist=custom)
        assert result == {"custom_field": "ok"}
        assert "_custom_field" not in result

    def test_non_scalar_dropped_with_custom_allowlist(self) -> None:
        custom = frozenset({"custom_field"})
        meta = {"custom_field": [1, 2, 3]}
        assert sanitize_platform_meta(meta, allowlist=custom) == {}
