"""Backward-compat re-export — FakeTtsWorker/FakeSttWorker moved to roxabi-nats.

ADR-059 V6: canonical location is now ``roxabi_nats.testing.voice``.
Update imports to avoid this shim in new code.
"""

try:
    from roxabi_nats.testing.voice import FakeSttWorker, FakeTtsWorker
except ImportError as exc:
    raise ImportError(
        "FakeTtsWorker and FakeSttWorker require roxabi-nats[testing]. "
        "Install it or add [tool.uv.sources] roxabi-nats = { git = ... } "
        "to your project. "
        f"Original error: {exc}"
    ) from exc

__all__: list[str] = ["FakeTtsWorker", "FakeSttWorker"]
