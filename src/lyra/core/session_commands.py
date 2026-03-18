"""Session command handlers for /add, /explain, /summarize (issue #99).

Each handler is an async function conforming to SessionCommandHandler protocol:
    async (msg, driver, tools, args, timeout) -> Response

Session commands make an isolated LLM call per invocation and never read or
write pool.sdk_history or pool.history.

pool_id convention: "session:<command>" (e.g. "session:add") — keeps log lines
identifiable and avoids polluting real pool history in the driver.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from lyra.core.agent_config import ModelConfig
from lyra.core.message import InboundMessage, Response
from lyra.integrations.base import ScrapeFailed, SessionTools, VaultWriteFailed

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


async def _scrape_with_fallback(tools: "SessionTools", url: str, timeout: float) -> str:
    """Scrape *url* via tools.scraper; return fallback string on any ScrapeFailed."""
    try:
        return await tools.scraper.scrape(url, timeout=timeout)
    except ScrapeFailed as exc:
        if exc.reason == "not_available":
            return f"[scraping unavailable] {url}"
        if exc.reason == "timeout":
            return f"[scrape timed out] {url}"
        return f"[scrape failed] {url}"


async def _llm_complete(
    pool_id: str,
    driver: "LlmProvider",
    system_prompt: str,
    content: str,
    error_prefix: str,
) -> tuple[str, None] | tuple[None, Response]:
    """Call driver.complete(); return (llm_text, None) on success or
    (None, error_response) on failure.

    Callers should unpack as::

        llm_text, err = await _llm_complete(...)
        if err is not None:
            return err
    """
    model_cfg = _make_model_cfg(driver)
    try:
        result = await driver.complete(
            pool_id,
            "",  # ignored when messages is provided
            model_cfg,
            system_prompt,
            messages=[{"role": "user", "content": content}],
        )
    except Exception:
        log.exception("%s: driver.complete() failed", pool_id)
        return None, Response(content=f"{error_prefix} Please try again.")

    if not result.ok:
        err_msg = result.user_message or "An error occurred. Please try again."
        return None, Response(content=err_msg)

    return result.result, None


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def cmd_add(
    msg: InboundMessage,
    driver: "LlmProvider",
    tools: "SessionTools",
    args: list[str],
    timeout: float,
) -> Response:
    """Save a URL to the vault: scrape → LLM summary → vault write."""
    if not args:
        return Response(content="Usage: /add <url>")

    url = args[0]

    scraped = await _scrape_with_fallback(tools, url, timeout=timeout / 3)

    llm_text, err = await _llm_complete(
        "session:add",
        driver,
        ADD_SYSTEM_PROMPT,
        scraped,
        "Failed to summarize the URL.",
    )
    if err is not None:
        return err
    assert llm_text is not None  # guaranteed by err check above

    title = _parse_title(llm_text, url)
    tags = _parse_tags(llm_text)

    # Vault write
    vault_note = ""
    try:
        await tools.vault.add(title, tags, url, llm_text, timeout=timeout / 3)
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
    tools: "SessionTools",
    args: list[str],
    timeout: float,
) -> Response:
    """Explain a URL in plain language: scrape → LLM explanation."""
    if not args:
        return Response(content="Usage: /explain <url>")

    url = args[0]

    scraped = await _scrape_with_fallback(tools, url, timeout=timeout / 3)

    llm_text, err = await _llm_complete(
        "session:explain",
        driver,
        EXPLAIN_SYSTEM_PROMPT,
        scraped,
        "Failed to explain the URL.",
    )
    if err is not None:
        return err
    assert llm_text is not None  # guaranteed by err check above

    return Response(content=llm_text)


async def cmd_summarize(
    msg: InboundMessage,
    driver: "LlmProvider",
    tools: "SessionTools",
    args: list[str],
    timeout: float,
) -> Response:
    """Summarize a URL in bullet points: scrape → LLM summary."""
    if not args:
        return Response(content="Usage: /summarize <url>")

    url = args[0]

    scraped = await _scrape_with_fallback(tools, url, timeout=timeout / 3)

    llm_text, err = await _llm_complete(
        "session:summarize",
        driver,
        SUMMARIZE_SYSTEM_PROMPT,
        scraped,
        "Failed to summarize the URL.",
    )
    if err is not None:
        return err
    assert llm_text is not None  # guaranteed by err check above

    return Response(content=llm_text)
