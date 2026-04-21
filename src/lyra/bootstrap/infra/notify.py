"""Hub startup notification — Telegram alert on successful launch."""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


async def notify_startup(active_proxies: list[str], health_port: int) -> None:
    """Send a Telegram notification on hub startup (best-effort)."""
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")
    if not token or not chat_id:
        log.debug("Startup notification skipped — TELEGRAM_TOKEN or CHAT_ID not set")
        return

    proxies = ", ".join(active_proxies) if active_proxies else "none"
    text = f"Lyra Hub started\n\nProxies: {proxies}\nHealth: :{health_port}"

    import httpx

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=10,
            )
        if resp.status_code != 200:
            log.warning("Startup notification failed: HTTP %d", resp.status_code)
        else:
            log.info("Startup notification sent to Telegram")
    except Exception as exc:
        log.warning("Startup notification failed: %s", exc)
