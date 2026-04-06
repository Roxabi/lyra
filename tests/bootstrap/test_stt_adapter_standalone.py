"""Tests for stt_adapter_standalone — RED-phase tests for NatsAdapterBase migration.

These tests verify the post-migration shape of stt_adapter_standalone.py:
  - No bare nats.connect() / import nats usage
  - STT_WORKERS constant is used (not a hardcoded QUEUE_GROUP string)
  - _bootstrap_stt_adapter_standalone retains its async signature
  - SttAdapterStandalone class exists and inherits from NatsAdapterBase
  - _mime_to_ext helper remains accessible at module level

Tests 1, 2, and 4 are RED before migration (they fail against current source).
Tests 3 and 5 pass both before and after migration (signature and helper unchanged).
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

_SOURCE = (
    Path(__file__).parent.parent.parent / "src/lyra/bootstrap/stt_adapter_standalone.py"
)


def test_stt_adapter_source_uses_no_bare_nats_connect() -> None:
    """Source must not contain bare nats.connect() or 'import nats' after migration.

    RED gate: current source has `nats.connect(` and `import nats\\n` — this fails
    until the migration to NatsAdapterBase removes them.
    """
    source = _SOURCE.read_text()
    assert "nats.connect(" not in source, (
        "stt_adapter_standalone still uses bare nats.connect()"
    )
    assert "import nats\n" not in source, (
        "stt_adapter_standalone still has bare 'import nats'"
    )


def test_stt_adapter_uses_stt_workers_constant() -> None:
    """Source must reference STT_WORKERS, not a hardcoded QUEUE_GROUP string.

    RED gate: current source uses a bare QUEUE_GROUP string; STT_WORKERS is not
    imported or defined until migration.
    """
    source = _SOURCE.read_text()
    assert "STT_WORKERS" in source, (
        "stt_adapter_standalone does not reference STT_WORKERS — "
        "migration must import and pass this constant to NatsAdapterBase"
    )


def test_bootstrap_stt_adapter_standalone_signature() -> None:
    """_bootstrap_stt_adapter_standalone must be async with the expected params.

    This test passes before and after migration — the public signature is unchanged.
    """
    from lyra.bootstrap.stt_adapter_standalone import _bootstrap_stt_adapter_standalone

    assert inspect.iscoroutinefunction(_bootstrap_stt_adapter_standalone), (
        "_bootstrap_stt_adapter_standalone must be a coroutine function"
    )

    sig = inspect.signature(_bootstrap_stt_adapter_standalone)
    params = list(sig.parameters)

    assert params[0] == "raw_config", (
        f"First parameter must be 'raw_config', got '{params[0]}'"
    )

    stop_param = sig.parameters.get("_stop")
    assert stop_param is not None, "Keyword-only parameter '_stop' must exist"
    assert stop_param.kind == inspect.Parameter.KEYWORD_ONLY, (
        "'_stop' must be a keyword-only parameter"
    )


def test_stt_adapter_standalone_class_exists() -> None:
    """SttAdapterStandalone must exist and subclass NatsAdapterBase after migration.

    RED gate: the class does not exist in current source — ImportError expected
    before migration.
    """
    from lyra.nats import NatsAdapterBase

    try:
        from lyra.bootstrap.stt_adapter_standalone import SttAdapterStandalone
    except ImportError as exc:
        pytest.fail(
            f"SttAdapterStandalone not found in stt_adapter_standalone — "
            f"migration has not been applied yet: {exc}"
        )

    assert issubclass(SttAdapterStandalone, NatsAdapterBase), (
        "SttAdapterStandalone must subclass NatsAdapterBase"
    )


def test_mime_to_ext_still_accessible() -> None:
    """_mime_to_ext must remain a module-level helper after migration.

    This test passes before and after migration — the helper is kept at module level.
    """
    from lyra.bootstrap.stt_adapter_standalone import _mime_to_ext

    assert _mime_to_ext("audio/ogg") == ".ogg", (
        "_mime_to_ext('audio/ogg') must return '.ogg'"
    )
    assert _mime_to_ext("audio/mpeg") == ".mp3", (
        "_mime_to_ext('audio/mpeg') must return '.mp3'"
    )
    assert _mime_to_ext("audio/wav") == ".wav", (
        "_mime_to_ext('audio/wav') must return '.wav'"
    )
    assert _mime_to_ext("audio/unknown") == ".ogg", (
        "_mime_to_ext with unknown MIME must fall back to '.ogg'"
    )
