"""Bootstrap package — startup wiring helpers extracted from __main__."""

from factory.bootstrap.factory.unified import _bootstrap_unified
from factory.bootstrap.standalone.adapter_standalone import (
    _bootstrap_adapter_standalone,
)
from factory.bootstrap.standalone.hub_standalone import _bootstrap_hub_standalone

__all__ = [
    "_bootstrap_unified",
    "_bootstrap_hub_standalone",
    "_bootstrap_adapter_standalone",
]
