#!/usr/bin/env bash
# DEPRECATED — host NATS retired by big-bang consolidation (see docs/ops/bigbang-nats-consolidation.md).
# Kept for rollback reference only. Do NOT run on a post-cutover system.
#
# Factory by Roxabi — NATS setup (install + configure + start)
#
# Usage: cd ~/projects/roxabi-factory && make nats-setup
#
# Does everything in one idempotent pass:
#   1. nats-server binary
#   2. nats system user + /etc/nats directories
#   3. nats.conf (install or update)
#   4. UFW firewall rule (port 4222, LAN only)
#   5. TLS certs (gen-certs.sh — skips if present)
#   6. nkey seeds (gen_nkeys.py — re-renders auth.conf + re-applies permissions on re-run)
#   7. Verify nkey enforcement is active
#
# Safe to re-run after upgrades, re-provisioning, or permission drift.
# To rotate keys: sudo rm -f /etc/nats/nkeys/auth.conf && rm -rf ~/.roxabi/factory/nkeys && make nats-setup

# ── RETIRED (#1930) ──────────────────────────────────────────────────────────
# The host nats.service is gone — production NATS is the rootless factory-nats
# container. This script (server binary, system user, TLS, UFW) no longer maps
# to the deployed topology and must NOT run on a post-cutover host. It is kept
# below for rollback-reference only. Fail fast and point at the live paths.
echo "ERROR: deploy/nats/setup.sh is retired — host NATS was replaced by the" >&2
echo "       containerised factory-nats unit (big-bang consolidation)." >&2
echo "       Cold-path key bootstrap:  make nats-setup   (factory-acl genkeys --ack-external-distribution)" >&2
echo "       Production deploy:         make converge" >&2
exit 1