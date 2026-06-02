# CLAUDE.md — src/factory/monitoring/

## Invariants

**STANDALONE** — invoked as `python -m lyra.monitoring` (valid triggers: manual / cron / CI smoke — ¬systemd timer).
¬imported by any other `src/factory/*` module. Only tests import this package.

**Exit-code contract** — `main()` returns and `SystemExit` propagates:
- `0` = all Layer 1 checks passed
- `1` = at least one check failed (anomaly)
Cron/timer consumers depend on this. ¬change without updating callers.

**Telegram side-effect** — any run that reaches Layer 2 sends a real message to the admin
chat. ¬run escalation paths in dev/CI against production credentials. Guard test runs
with mock config (no `TELEGRAM_TOKEN` / `TELEGRAM_ADMIN_CHAT_ID`).

## Two-tier escalation

```
Layer 1: run_checks()          → fast, deterministic, zero LLM tokens
          all_passed → exit 0
          anomaly    ──────────────────────────────────────────────┐
                                                                   ↓
Layer 2: escalate_to_llm()     → Claude CLI (OAuth), 30 s timeout
          DiagnosisReport ──→ send_telegram_alert()    → formatted message
          LLM unavailable  ──→ send_telegram_raw_alert() → raw fallback
          Telegram fails   ──→ log-only, still exit 1
```

This monitoring process is the **safety-net catch** for persistent anomalies, not the
primary observability path.

## Config

Thresholds: `[monitoring]` section in lyra.toml (or `$LYRA_CONFIG`).
Secrets (required at runtime, ¬in TOML): `TELEGRAM_TOKEN`, `TELEGRAM_ADMIN_CHAT_ID`.
Optional: `LYRA_HEALTH_SECRET` (Bearer token for `/health/detail`).
Missing secrets → `ValueError` at startup (fail-fast, ¬silent misconfiguration).

## Operational notes

- `checks_varz.py` writes state to `~/.lyra/nats-monitor-state.json` to detect deltas across
  runs. ¬delete this file without expecting a spurious alert on the next run.
- LLM backend: `claude` CLI (OAuth, ¬API key). If absent, falls back to raw Telegram alert.
