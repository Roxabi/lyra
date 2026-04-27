"""Backward-compat re-export — FakeImageWorker moved to roxabi-nats[testing].

ADR-059 V6: canonical location is now ``roxabi_nats.testing.image``.
Update imports to avoid this shim in new code.
"""

from roxabi_nats.testing.image import FakeImageWorker

__all__: list[str] = ["FakeImageWorker"]
