"""Entry point: python -m lyra.

Starts Telegram + Discord adapters in one event loop.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from dotenv import load_dotenv

from lyra.adapters.discord import DiscordAdapter, load_discord_config
from lyra.adapters.telegram import TelegramAdapter
from lyra.adapters.telegram import load_config as load_telegram_config
from lyra.core.hub import Hub
from lyra.core.message import Platform

log = logging.getLogger(__name__)


async def _main(*, _stop: asyncio.Event | None = None) -> None:
    """Wire hub + adapters and run until stop event fires.

    The optional _stop parameter is for testing: pass a pre-set Event to exit
    immediately after setup without registering signal handlers.
    """
    load_dotenv()

    # Config loaders call sys.exit() on missing required env vars — no partial startup.
    tg_cfg = load_telegram_config()
    dc_cfg = load_discord_config()

    hub = Hub()

    tg_adapter = TelegramAdapter(
        bot_id="main",
        token=tg_cfg.token,
        hub=hub,
        bot_username=tg_cfg.bot_username,
        webhook_secret=tg_cfg.webhook_secret,
    )
    dc_adapter = DiscordAdapter(hub=hub, bot_id="main")

    hub.register_adapter(Platform.TELEGRAM, "main", tg_adapter)
    hub.register_adapter(Platform.DISCORD, "main", dc_adapter)
    hub.register_binding(Platform.TELEGRAM, "main", "*", "lyra", "telegram:main:*")
    hub.register_binding(Platform.DISCORD, "main", "*", "lyra", "discord:main:*")

    stop = _stop if _stop is not None else asyncio.Event()
    if _stop is None:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, stop.set)
        loop.add_signal_handler(signal.SIGTERM, stop.set)

    tasks = [
        asyncio.create_task(hub.run(), name="hub"),
        asyncio.create_task(
            # handle_signals=False is mandatory when aiogram runs inside
            # asyncio.gather() — otherwise it installs its own signal handlers
            # that conflict with the event loop's, causing double-cancellation.
            tg_adapter.dp.start_polling(tg_adapter.bot, handle_signals=False),
            name="telegram",
        ),
        asyncio.create_task(dc_adapter.start(dc_cfg.token), name="discord"),
    ]

    log.info("Lyra started — Telegram + Discord adapters running.")

    await stop.wait()

    log.info("Shutdown signal received — stopping…")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await dc_adapter.close()
    log.info("Lyra stopped.")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(_main())


if __name__ == "__main__":
    main()
