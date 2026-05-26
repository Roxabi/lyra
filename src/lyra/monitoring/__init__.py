"""Monitoring package — Python module retained for spec reference.

The host-timer units (lyra-monitor.{service,timer}) have been removed from
deploy/. This module remains as the canonical health-probe implementation
(`python -m lyra.monitoring`) and a reference for Monitoring v2 (#1035).
"""

import warnings

warnings.warn(
    "lyra.monitoring is deprecated — superseded by Monitoring v2 (#1035). "
    "This package will be removed when v2 lands.",
    DeprecationWarning,
    stacklevel=1,
)
