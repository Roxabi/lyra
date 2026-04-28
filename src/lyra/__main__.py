"""Entry point: python -m lyra.

Starts hub + adapters in one event loop with NATS message bus.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from lyra.bootstrap.factory.config import (
    _load_logging_config,
    _load_raw_config,
)
from lyra.bootstrap.factory.unified import _bootstrap_unified
from lyra.core.trace import TelegramTokenFilter, TraceIdFilter
from lyra.errors import KeyringError, MissingCredentialsError

log = logging.getLogger(__name__)


async def _main(*, _stop: asyncio.Event | None = None) -> None:
    """Wire hub + adapters and run until stop event fires.

    The optional _stop parameter is for testing: pass a pre-set Event to exit
    immediately after setup without registering signal handlers.
    """
    load_dotenv()
    if not os.environ.get("LYRA_HEALTH_SECRET"):
        log.warning(
            "LYRA_HEALTH_SECRET is not set -- /health returns minimal response only"
        )
    raw_config = _load_raw_config()

    try:
        await _bootstrap_unified(raw_config, _stop=_stop)
    except (MissingCredentialsError, KeyringError) as exc:
        sys.exit(str(exc))


def _setup_logging(level: str = "INFO") -> None:
    """Configure logging: stdout console handler only.

    Uses explicit handler construction (not ``basicConfig``) to guarantee
    formatter and filter attachment.
    """
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"

    trace_filter = TraceIdFilter()
    # Redact Telegram bot tokens — httpx logs full request URLs (incl. token)
    # at INFO. Attached BEFORE formatting.
    telegram_token_filter = TelegramTokenFilter()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(fmt))
    console_handler.addFilter(trace_filter)
    console_handler.addFilter(telegram_token_filter)

    root = logging.getLogger()
    if root.handlers:
        return  # already configured — avoid duplicate handlers
    level_int = getattr(logging, level.upper(), logging.INFO)
    root.setLevel(level_int)
    root.addFilter(trace_filter)
    root.addFilter(telegram_token_filter)
    root.addHandler(console_handler)


def main() -> None:
    raw_config = _load_raw_config()
    _setup_logging(_load_logging_config(raw_config).level)
    asyncio.run(_main())


if __name__ == "__main__":
    main()
