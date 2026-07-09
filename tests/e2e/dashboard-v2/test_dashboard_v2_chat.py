"""Functional Playwright test — chat composer to assistant bubble (AG-UI e2e stub)."""

from __future__ import annotations

import re

import pytest


def test_chat_send_shows_assistant_reply(dashboard_v2_url: str) -> None:
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(f"{dashboard_v2_url}chat", wait_until="networkidle")
        textarea = page.locator("textarea").first
        textarea.wait_for(state="visible", timeout=10_000)
        page.wait_for_function(
            "() => { const el = document.querySelector('textarea');"
            " return el && !el.disabled; }",
            timeout=15_000,
        )
        textarea.fill("Hello E2E")
        page.get_by_role("button", name=re.compile(r"Send|Envoyer", re.I)).click()
        page.get_by_text("E2E stub reply", exact=False).wait_for(timeout=15_000)
        browser.close()
