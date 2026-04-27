"""Bot-to-agent mapping resolution for multibot bootstrap."""

from __future__ import annotations

import logging

from lyra.config import DiscordBotConfig, TelegramBotConfig
from lyra.infrastructure.stores.agent_store import AgentStore

log = logging.getLogger(__name__)


async def resolve_bot_agent_map(
    agent_store: AgentStore,
    tg_bots: "list[TelegramBotConfig]",
    dc_bots: "list[DiscordBotConfig]",
) -> "dict[tuple[str, str], str]":
    """Resolve (platform, bot_id) -> agent_name for all configured bots.

    Resolution order per bot:
      1. bot_agent_map DB row (agent_store.get_bot_agent)
      2. bot_cfg.agent from TOML — auto-seeds bot_agent_map in DB
      3. Neither -> log error, skip (bot not included in result)

    Bots whose resolved agent name is not found in the agents table are also
    logged and skipped (adapter cannot be wired without a valid agent row).

    Returns dict mapping (platform, bot_id) -> agent_name for bots that can
    be safely wired.
    """
    result: dict[tuple[str, str], str] = {}

    all_bots: list[tuple[str, str, str | None]] = []
    for bot_cfg in tg_bots:
        toml_agent = getattr(bot_cfg, "agent", None)
        all_bots.append(("telegram", bot_cfg.bot_id, toml_agent))
    for bot_cfg in dc_bots:
        toml_agent = getattr(bot_cfg, "agent", None)
        all_bots.append(("discord", bot_cfg.bot_id, toml_agent))

    for platform, bot_id, toml_agent in all_bots:
        # 1. Check DB cache (bot_agent_map table)
        db_agent_name = agent_store.get_bot_agent(platform, bot_id)

        if db_agent_name is not None:
            # DB row found — validate the referenced agent exists in agents table.
            # A stale bot_agent_map row (agent deleted) is a configuration error.
            if agent_store.get(db_agent_name) is None:
                log.error(
                    "bot_agent_map: agent %r for (%r, %r) not found in"
                    " agents table — skipping adapter",
                    db_agent_name,
                    platform,
                    bot_id,
                )
                continue
            result[(platform, bot_id)] = db_agent_name
        elif toml_agent:
            # Check agent exists in DB before seeding the mapping
            if agent_store.get(toml_agent) is None:
                log.error(
                    "bot_agent_map: TOML agent %r for (%r, %r) not found in agents DB "
                    "— skipping adapter (run 'lyra agent init' to import TOMLs)",
                    toml_agent,
                    platform,
                    bot_id,
                )
                continue
            # 2. No DB row — fall back to TOML bot_cfg.agent, seed bot_agent_map.
            log.info(
                "bot_agent_map: no DB row for (%r, %r) — seeding from TOML agent=%r",
                platform,
                bot_id,
                toml_agent,
            )
            try:
                await agent_store.set_bot_agent(platform, bot_id, toml_agent)
            except Exception as exc:  # noqa: BLE001 — resilient: DB seed failure must not abort multibot startup
                log.warning(
                    "bot_agent_map: failed to seed (%r, %r) -> %r: %s",
                    platform,
                    bot_id,
                    toml_agent,
                    exc,
                )
            result[(platform, bot_id)] = toml_agent
        else:
            # 3. No DB row, no TOML agent — skip
            log.error(
                "bot_agent_map: no DB row and no TOML agent for"
                " (%r, %r) — skipping adapter",
                platform,
                bot_id,
            )
            continue

    return result
