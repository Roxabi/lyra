"""Lyra monitoring: two-layer health check system (issue #111).

DEPRECATED — superseded by Monitoring v2 (#1035): NATS event stream + Tauri
desktop dashboard. Do not extend this package. The host-timer pattern (systemctl
+ podman logs + loopback HTTP) cannot be containerized cleanly, polls instead
of pushing, and offers no UI beyond a Telegram message. This module's check
logic (process state, log scan, NATS varz, disk, idle, queue depth, circuits,
reaper) is preserved for the v2 spec author to mine. The host timer has been
disabled on prod; this package will be deleted when #1035 lands.
"""
