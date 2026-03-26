"""Entry point: python -m lyra.

Starts Telegram + Discord adapters in one event loop.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

# Re-export adapters so test monkeypatching on main_mod still works.
# The bootstrap modules import these directly; tests that patch main_mod.*
# must also patch the bootstrap module namespace (see tests/test_main.py).
from lyra.adapters.discord import (
    DiscordAdapter,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from lyra.adapters.telegram import (
    TelegramAdapter,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from lyra.bootstrap.config import (
    _load_circuit_config,
    _load_raw_config,
)
from lyra.bootstrap.multibot import _bootstrap_multibot
from lyra.config import (
    DiscordMultiConfig,
    TelegramMultiConfig,
    load_multibot_config,
)
from lyra.errors import KeyringError, MissingCredentialsError

log = logging.getLogger(__name__)


def _parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse daemon startup arguments."""
    parser = argparse.ArgumentParser(
        description="Lyra daemon — start Telegram/Discord adapters",
        add_help=True,
    )
    parser.add_argument(
        "--adapter",
        choices=["telegram", "discord", "all"],
        default="all",
        help=(
            "Which adapter(s) to start. "
            "'telegram' starts only Telegram adapters, "
            "'discord' starts only Discord adapters, "
            "'all' starts both (default)."
        ),
    )
    return parser.parse_args(args)


async def _main(*, adapter: str = "all", _stop: asyncio.Event | None = None) -> None:
    """Wire hub + adapters and run until stop event fires.

    The optional _stop parameter is for testing: pass a pre-set Event to exit
    immediately after setup without registering signal handlers.

    Supports two configuration schemas:
      - New multi-bot: [[telegram.bots]] / [[discord.bots]] arrays in config.toml.
      - Legacy single-bot: [auth.telegram] / [auth.discord] flat sections
        (backward compat -- synthesised into bot_id="main" by load_multibot_config).

    Both schemas are handled by the same bootstrap path (_bootstrap_multibot).
    """
    load_dotenv()
    if not os.environ.get("LYRA_HEALTH_SECRET"):
        log.warning(
            "LYRA_HEALTH_SECRET is not set -- /health returns minimal response only"
        )
    raw_config = _load_raw_config()
    circuit_registry, admin_user_ids = _load_circuit_config(raw_config)
    try:
        tg_multi_cfg, dc_multi_cfg = load_multibot_config(raw_config)
    except ValueError as exc:
        sys.exit(str(exc))

    # Apply adapter filter BEFORE bootstrap -- guard evaluates post-filter state
    original_tg_count = len(tg_multi_cfg.bots)
    original_dc_count = len(dc_multi_cfg.bots)
    if adapter == "telegram":
        dc_multi_cfg = DiscordMultiConfig(bots=[])
    elif adapter == "discord":
        tg_multi_cfg = TelegramMultiConfig(bots=[])

    # Guard: adapter flag specified but no matching bots in config
    if adapter != "all" and not (tg_multi_cfg.bots or dc_multi_cfg.bots):
        requested_count = (
            original_tg_count if adapter == "telegram" else original_dc_count
        )
        if requested_count == 0:
            sys.exit(
                f"--adapter {adapter} specified but no [[{adapter}.bots]] entries found"
                " in config.toml -- add at least one bot under [[{adapter}.bots]]"
                " or omit --adapter to start all configured adapters"
            )

    try:
        await _bootstrap_multibot(
            raw_config,
            circuit_registry,
            admin_user_ids,
            tg_multi_cfg,
            dc_multi_cfg,
            _stop=_stop,
        )
    except (MissingCredentialsError, KeyringError) as exc:
        sys.exit(str(exc))


def _setup_logging() -> None:
    """Configure logging: console + rotating file in ~/.local/state/lyra/logs/."""
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"

    log_dir = Path.home() / ".local" / "state" / "lyra" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"{stamp}_lyra.log"

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,  # 10 MB per file, 5 backups
    )
    file_handler.setFormatter(logging.Formatter(fmt))

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(fmt))

    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])

    logging.getLogger(__name__).info("Logging to %s", log_file)


def main() -> None:
    _setup_logging()
    parsed = _parse_args()
    asyncio.run(_main(adapter=parsed.adapter))


if __name__ == "__main__":
    main()
