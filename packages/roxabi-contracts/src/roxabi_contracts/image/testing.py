"""Backward-compat re-export — FakeImageWorker moved to roxabi-nats[testing].

ADR-059 V6: canonical location is now ``roxabi_nats.testing.image``.
Update imports to avoid this shim in new code.
"""

try:
    from roxabi_nats.testing.image import FakeImageWorker
except ImportError as exc:
    raise ImportError(
        "FakeImageWorker requires roxabi-nats[testing]. "
        "Install it or add [tool.uv.sources] roxabi-nats = { git = ... } "
        "to your project. "
        f"Original error: {exc}"
    ) from exc

__all__: list[str] = ["FakeImageWorker"]
