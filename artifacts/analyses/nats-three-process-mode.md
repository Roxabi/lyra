# Lyra — NATS Three-Process Production Mode

**Status:** Active (as of 2026-03-31, closes NATS epic #445)
**ADR:** ADR-037
**Replaces:** Single-process `_bootstrap_multibot` mode

---

## Overview

Lyra now runs as **three separate supervisor processes** instead of one. The hub and each adapter are independent OS processes that communicate exclusively through NATS subjects.

```
lyra_hub          ←→  NATS  ←→  lyra_telegram
                              ←→  lyra_discord
```

---

## Processes

| Supervisor program | CLI command | Bootstrap function | Source |
|-------------------|-------------|-------------------|--------|
| `lyra_hub` | `lyra hub` | `_bootstrap_hub_standalone()` | `bootstrap/hub_standalone.py` |
| `lyra_telegram` | `lyra adapter telegram` | `_bootstrap_adapter_standalone()` | `bootstrap/adapter_standalone.py` |
| `lyra_discord` | `lyra adapter discord` | `_bootstrap_adapter_standalone()` | `bootstrap/adapter_standalone.py` |

Scripts in lyra-stack supervisor conf: `run_hub.sh` (hub) · `run_adapter.sh` (adapters, parameterised by platform).

---

## NATS Topics

| Direction | Subject pattern |
|-----------|----------------|
| Adapter → Hub | `lyra.inbound.<platform>.<bot_id>` |
| Hub → Adapter | `lyra.outbound.<platform>.<bot_id>` |

Example: Telegram bot `tg_main` publishes inbound messages on `lyra.inbound.telegram.tg_main` and listens for replies on `lyra.outbound.telegram.tg_main`.

---

## What Each Process Does

**`lyra_hub`**
- Loads all agents, pools, LLM config, memory, auth DB
- Subscribes to `lyra.inbound.*.*` — receives messages from all adapters
- Runs the full turn loop (LLM call, tool dispatch, memory writes)
- Publishes replies to `lyra.outbound.<platform>.<bot_id>`
- Holds the auth DB open; owns session and pool state

**`lyra_telegram` / `lyra_discord`**
- Thin NATS client — no local Hub, no agents, no LLM
- Reads credentials from the encrypted store at startup, then closes the store
- Runs the platform connection (aiogram long-polling / discord.py websocket)
- Normalises inbound messages → publishes to `lyra.inbound.<platform>.<bot_id>`
- Listens on `lyra.outbound.<platform>.<bot_id>` → sends replies to users

---

## Management

```bash
# From lyra-stack (canonical)
make lyra            # status: lyra_hub + lyra_telegram + lyra_discord
make lyra reload     # restart all three
make lyra logs       # tail lyra_hub stdout
make lyra errors     # tail lyra_hub stderr

make telegram        # lyra_telegram only (start|reload|stop|logs|errors)
make discord         # lyra_discord only  (start|reload|stop|logs|errors)

# From lyra/ project dir (delegates to lyra-stack)
make lyra            # same
make lyra stop       # stop all three
make lyra status     # status of all three
```

---

## Old Mode (still in codebase, not production)

```bash
python -m lyra --adapter telegram   # → _bootstrap_multibot()
```

Ran hub + adapter(s) in a single process. Still exists in the codebase for local dev convenience but is **not the production deployment mode**. Do not reference it in new documentation or new code paths.

---

## Why Three Processes

- **Fault isolation** — an adapter crash does not kill the hub or the other adapter
- **Independent restarts** — restart lyra_telegram without flushing hub session state
- **Future scale** — hub and adapters can move to different machines (already the design intent of ADR-037)
- **Cleaner separation** — adapters have zero knowledge of agents, LLM, or memory; they are pure message forwarders

---

## Key Files

| File | Role |
|------|------|
| `src/lyra/bootstrap/hub_standalone.py` | Hub bootstrap — wires NATS, agents, LLM, buses |
| `src/lyra/bootstrap/adapter_standalone.py` | Adapter bootstrap — platform connection + NATS publish/subscribe |
| `src/lyra/adapters/nats_outbound_listener.py` | Adapter side: listens for outbound replies from hub |
| `src/lyra/nats/nats_bus.py` | `NatsBus` — `Bus[T]` implementation over NATS |
| `src/lyra/cli.py` | `lyra hub` and `lyra adapter <platform>` CLI entry points |
| `docs/adr/037-*.md` | ADR-037 — decision record for this architecture |
