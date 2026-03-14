"""Session command handlers for /add, /explain, /summarize (issue #99).

Each handler is an async function conforming to SessionCommandHandler protocol:
    async (msg, driver, args, timeout) -> Response

Session commands make an isolated LLM call per invocation and never read or
write pool.sdk_history or pool.history.

pool_id convention: "session:<command>" (e.g. "session:add") — keeps log lines
identifiable and avoids polluting real pool history in the driver.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from lyra.core.agent import ModelConfig
from lyra.core.message import InboundMessage, Response
from lyra.core.session_helpers import (
    ScrapeFailed,
    VaultWriteFailed,
    scrape_url,
    vault_add,
)

if TYPE_CHECKING:
    from lyra.llm.base import LlmProvider

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

ADD_SYSTEM_PROMPT = (
    "You are a knowledge base assistant. Given web content, extract: "
    "Title: <title>, Summary: <one paragraph>, Tags: <3-5 comma-separated tags>. "
    "Be concise."
)

EXPLAIN_SYSTEM_PROMPT = (
    "You are a helpful assistant. Explain the following web content in plain language "
    "suitable for sharing in a chat message. Be concise and focus on key points."
)

SUMMARIZE_SYSTEM_PROMPT = (
    "You are a helpful assistant. Summarize the following web content in 3-5 bullet "
    "points. Be concise."
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TITLE_RE = re.compile(r"^Title:\s*(.+)$", re.MULTILINE)
_TAGS_RE = re.compile(r"^Tags:\s*(.+)$", re.MULTILINE)

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def _parse_title(text: str, fallback: str) -> str:
    m = _TITLE_RE.search(text)
    return m.group(1).strip() if m else fallback


def _parse_tags(text: str) -> list[str]:
    m = _TAGS_RE.search(text)
    if not m:
        return []
    return [t.strip() for t in m.group(1).split(",") if t.strip()]


def _make_model_cfg(driver: "LlmProvider") -> ModelConfig:
    """Build a minimal ModelConfig for a session LLM call."""
    model = driver.capabilities.get("default_model", _DEFAULT_MODEL)
    if not model:
        model = _DEFAULT_MODEL
    return ModelConfig(backend="anthropic-sdk", model=str(model))


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def cmd_add(
    msg: InboundMessage,
    driver: "LlmProvider",
    args: list[str],
    timeout: float,
) -> Response:
    """Save a URL to the vault: scrape → LLM summary → vault write."""
    if not args:
        return Response(content="Usage: /add <url>")

    url = args[0]

    # Scrape
    try:
        scraped = await scrape_url(url, timeout=timeout / 3)
    except ScrapeFailed as exc:
        if exc.reason == "not_available":
            scraped = f"[scraping unavailable] {url}"
        elif exc.reason == "timeout":
            scraped = f"[scrape timed out] {url}"
        else:
            scraped = f"[scrape failed] {url}"

    # LLM summary
    model_cfg = _make_model_cfg(driver)
    try:
        result = await driver.complete(
            "session:add",
            "",  # ignored when messages is provided
            model_cfg,
            ADD_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": scraped}],
        )
    except Exception:
        log.exception("cmd_add: driver.complete() failed")
        return Response(content="Failed to summarize the URL. Please try again.")

    if not result.ok:
        return Response(content=f"LLM error: {result.error}")

    llm_text = result.result
    title = _parse_title(llm_text, url)
    tags = _parse_tags(llm_text)

    # Vault write
    vault_note = ""
    try:
        await vault_add(title, tags, url, llm_text, timeout=timeout / 3)
    except VaultWriteFailed as exc:
        log.warning("cmd_add: vault write failed (%s)", exc)
        if exc.args and exc.args[0] == "not_available":
            vault_note = "\n\n(vault CLI not available — summary not saved)"
        else:
            vault_note = "\n\n(vault write failed — summary not saved)"

    return Response(content=f"Saved: {title}\n\n{llm_text}{vault_note}")


async def cmd_explain(
    msg: InboundMessage,
    driver: "LlmProvider",
    args: list[str],
    timeout: float,
) -> Response:
    """Explain a URL in plain language: scrape → LLM explanation."""
    if not args:
        return Response(content="Usage: /explain <url>")

    url = args[0]

    try:
        scraped = await scrape_url(url, timeout=timeout / 3)
    except ScrapeFailed as exc:
        if exc.reason == "not_available":
            scraped = f"[scraping unavailable] {url}"
        elif exc.reason == "timeout":
            scraped = f"[scrape timed out] {url}"
        else:
            scraped = f"[scrape failed] {url}"

    model_cfg = _make_model_cfg(driver)
    try:
        result = await driver.complete(
            "session:explain",
            "",  # ignored when messages is provided
            model_cfg,
            EXPLAIN_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": scraped}],
        )
    except Exception:
        log.exception("cmd_explain: driver.complete() failed")
        return Response(content="Failed to explain the URL. Please try again.")

    if not result.ok:
        return Response(content=f"LLM error: {result.error}")

    return Response(content=result.result)


async def cmd_summarize(
    msg: InboundMessage,
    driver: "LlmProvider",
    args: list[str],
    timeout: float,
) -> Response:
    """Summarize a URL in bullet points: scrape → LLM summary."""
    if not args:
        return Response(content="Usage: /summarize <url>")

    url = args[0]

    try:
        scraped = await scrape_url(url, timeout=timeout / 3)
    except ScrapeFailed as exc:
        if exc.reason == "not_available":
            scraped = f"[scraping unavailable] {url}"
        elif exc.reason == "timeout":
            scraped = f"[scrape timed out] {url}"
        else:
            scraped = f"[scrape failed] {url}"

    model_cfg = _make_model_cfg(driver)
    try:
        result = await driver.complete(
            "session:summarize",
            "",  # ignored when messages is provided
            model_cfg,
            SUMMARIZE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": scraped}],
        )
    except Exception:
        log.exception("cmd_summarize: driver.complete() failed")
        return Response(content="Failed to summarize the URL. Please try again.")

    if not result.ok:
        return Response(content=f"LLM error: {result.error}")

    return Response(content=result.result)
