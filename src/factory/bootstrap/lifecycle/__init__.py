"""Lifecycle bootstrap — lifecycle orchestration and signal handling."""

from .bootstrap_lifecycle import run_lifecycle
from .lifecycle_helpers import (
    setup_signal_handlers,
    teardown_buses,
    teardown_dispatchers,
)
from .signal_handlers import setup_shutdown_event

__all__ = [
    "run_lifecycle",
    "setup_signal_handlers",
    "teardown_buses",
    "teardown_dispatchers",
    "setup_shutdown_event",
]
