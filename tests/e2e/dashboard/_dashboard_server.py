"""Standalone uvicorn entry for Playwright visual tests."""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import uvicorn

from factory.adapters.web.web_adapter import WebAdapter
from factory.adapters.web.web_server import create_app


def main() -> None:
    port = int(sys.argv[1])
    os.environ.setdefault("FACTORY_DASHBOARD_E2E", "1")
    bus = MagicMock()
    bus.put = AsyncMock()
    adapter = WebAdapter(
        inbound_bus=bus,
        agent_names=["alpha", "beta"],
        port=19999,
    )
    listener = MagicMock()
    listener.cache_inbound = MagicMock()
    adapter._outbound_listener = listener
    uvicorn.run(create_app(adapter), host="127.0.0.1", port=port, log_level="error")


if __name__ == "__main__":
    main()