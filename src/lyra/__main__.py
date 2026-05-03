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
from lyra.core.logging_setup import setup_logging
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


def main() -> None:
    raw_config = _load_raw_config()
    setup_logging(_load_logging_config(raw_config).level)
    asyncio.run(_main())


if __name__ == "__main__":
    main()
