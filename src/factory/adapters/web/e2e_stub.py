"""E2E AG-UI chat stub for Playwright/CI (no NATS round-trip)."""

from __future__ import annotations

import os
from typing import Any


def e2e_enabled() -> bool:
    return os.environ.get("FACTORY_DASHBOARD_E2E", "").strip() in {"1", "true", "yes"}


async def publish_e2e_agui_reply(
    sessions: Any,
    session_id: str,
    user_text: str,
) -> None:
    """Publish a minimal AG-UI sequence after POST /api/chat in e2e mode."""
    from factory.adapters.web import web_agui

    run_id = web_agui.new_run_id()
    message_id = web_agui.new_message_id()
    reply = f"E2E stub reply to: {user_text[:80]}"
    publish = sessions.publish
    await publish(session_id, web_agui.run_started(thread_id=session_id, run_id=run_id))
    await publish(session_id, web_agui.text_start(message_id=message_id))
    await publish(
        session_id,
        web_agui.text_content(message_id=message_id, delta=reply),
    )
    await publish(session_id, web_agui.text_end(message_id=message_id))
    await publish(
        session_id,
        web_agui.run_finished(thread_id=session_id, run_id=run_id),
    )
