"""Lightweight HttpBlobStore factory for adapters.

Reads LYRA_BLOBSTORE_URL and LYRA_BLOBSTORE_TOKEN_PATH from environment.
Defaults: http://localhost:8449, ~/.lyra/blobstore.tok
"""

from __future__ import annotations

import os
from pathlib import Path

from roxabi_blobs import HttpBlobStore


def get_blobstore_client() -> HttpBlobStore:
    base_url = os.environ.get("LYRA_BLOBSTORE_URL", "http://localhost:8449")
    token_path = os.environ.get(
        "LYRA_BLOBSTORE_TOKEN_PATH",
        str(Path.home() / ".lyra" / "blobstore.tok"),
    )
    token = Path(token_path).read_text().strip()
    return HttpBlobStore(base_url, token)
