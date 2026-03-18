"""Tests for RuntimeConfig.debounce_ms — validation and reset (issue #145)."""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# RuntimeConfig — debounce_ms validation
# ---------------------------------------------------------------------------


class TestRuntimeConfigDebounceMs:
    """debounce_ms field in RuntimeConfig."""

    def test_default_value(self) -> None:
        from lyra.core.runtime_config import RuntimeConfig

        rc = RuntimeConfig()
        assert rc.debounce_ms == 300

    def test_set_valid(self) -> None:
        from lyra.core.runtime_config import RuntimeConfig, set_param

        rc = RuntimeConfig()
        rc = set_param(rc, "debounce_ms", "500")
        assert rc.debounce_ms == 500

    def test_set_zero(self) -> None:
        from lyra.core.runtime_config import RuntimeConfig, set_param

        rc = RuntimeConfig()
        rc = set_param(rc, "debounce_ms", "0")
        assert rc.debounce_ms == 0

    def test_reject_negative(self) -> None:
        from lyra.core.runtime_config import RuntimeConfig, set_param

        rc = RuntimeConfig()
        with pytest.raises(ValueError, match="between 0 and 5000"):
            set_param(rc, "debounce_ms", "-1")

    def test_reject_too_large(self) -> None:
        from lyra.core.runtime_config import RuntimeConfig, set_param

        rc = RuntimeConfig()
        with pytest.raises(ValueError, match="between 0 and 5000"):
            set_param(rc, "debounce_ms", "6000")

    def test_reject_non_integer(self) -> None:
        from lyra.core.runtime_config import RuntimeConfig, set_param

        rc = RuntimeConfig()
        with pytest.raises(ValueError, match="integer"):
            set_param(rc, "debounce_ms", "abc")

    def test_reset_to_default(self) -> None:
        from lyra.core.runtime_config import RuntimeConfig, set_param

        rc = set_param(RuntimeConfig(), "debounce_ms", "500")
        rc = RuntimeConfig.reset(rc, "debounce_ms")
        assert rc.debounce_ms == 300
