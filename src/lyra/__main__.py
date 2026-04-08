"""Entry point: python -m lyra.

Starts hub + adapters in one event loop with NATS message bus.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

from lyra.bootstrap.config import (
    LoggingConfig,
    _load_logging_config,
    _load_raw_config,
)
from lyra.bootstrap.unified import _bootstrap_unified
from lyra.core.trace import JsonFormatter, TraceIdFilter
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
            "LYRA_HEALTH_SECRET is not set -- /health returns minimal"
            " response only"
        )
    raw_config = _load_raw_config()

    try:
        await _bootstrap_unified(raw_config, _stop=_stop)
    except (MissingCredentialsError, KeyringError) as exc:
        sys.exit(str(exc))


def _setup_logging(log_config: LoggingConfig | None = None) -> None:
    """Configure logging: console + rotating file in ~/.local/state/lyra/logs/.

    Uses explicit handler construction (not ``basicConfig``) to guarantee
    formatter and filter attachment.  When ``log_config.json_file`` is True
    the file handler emits JSONL; console always stays plaintext.
    """
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    if log_config is None:
        log_config = LoggingConfig()

    _default_log = str(Path.home() / ".local" / "state" / "lyra" / "logs")
    log_dir = Path(os.environ.get("LYRA_LOG_DIR", _default_log))
    log_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"{stamp}_lyra.log"

    trace_filter = TraceIdFilter()

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,  # 10 MB per file, 5 backups
    )
    if log_config.json_file:
        file_handler.setFormatter(JsonFormatter())
    else:
        file_handler.setFormatter(logging.Formatter(fmt))
    file_handler.addFilter(trace_filter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(fmt))
    console_handler.addFilter(trace_filter)

    root = logging.getLogger()
    if root.handlers:
        return  # already configured — avoid duplicate handlers
    root.setLevel(logging.INFO)
    root.addFilter(trace_filter)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    logging.getLogger(__name__).info("Logging to %s", log_file)


def main() -> None:
    raw_config = _load_raw_config()
    log_config = _load_logging_config(raw_config)
    _setup_logging(log_config)
    asyncio.run(_main())


if __name__ == "__main__":
    main()
