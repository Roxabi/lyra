"""Monitoring package — mostly retained for spec reference, two modules live.

The host-timer units (lyra-monitor.{service,timer}) have been removed from
deploy/. Most of this package (`checks.py`'s aggregate Layer-1 runner,
`check_process`/`check_http_health`, `__main__.py`) remains the canonical
health-probe implementation (`python -m factory.monitoring`) and a reference
for Monitoring v2 (#1035) — that path still warns `DeprecationWarning` on
import (see `checks.py`).

`checks_log.py`, `log_watch.py`, and `escalation.py`'s raw-alert path are the
exception: as of #2245 they are live production code, run continuously by the
standalone `factory-log-monitor` Quadlet container as a thin V1-pull safety
net (ADR-091 plane③). They do not warn on import and should be retired or
subsumed once Monitoring v2 (#1035) ships log-scanning as a hub-native
consumer.
"""
