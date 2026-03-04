"""conftest.py for adapters tests.

Patches httpx.Cookies.extract_cookies to handle relative URLs gracefully.
httpx 0.28 requires absolute URLs for cookie extraction (urllib.request.Request
raises ValueError on relative URLs). Since ASGI tests don't need cookie jar
functionality, we skip extraction when the request URL is relative.
"""

from __future__ import annotations

import httpx
import pytest

_original_extract_cookies = httpx.Cookies.extract_cookies


def _safe_extract_cookies(self, response: httpx.Response) -> None:
    """Skip cookie extraction for responses to relative-URL requests."""
    try:
        _original_extract_cookies(self, response)
    except ValueError:
        # Relative URL in ASGI transport test — no cookies to extract.
        pass


@pytest.fixture(autouse=True)
def patch_httpx_cookies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto-use fixture: make httpx.Cookies.extract_cookies safe with relative URLs."""
    monkeypatch.setattr(httpx.Cookies, "extract_cookies", _safe_extract_cookies)
