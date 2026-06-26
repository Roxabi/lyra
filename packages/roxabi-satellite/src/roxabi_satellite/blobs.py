"""HttpBlobStore singleton + BlobRef wire bridge for NATS satellites (ADR-068)."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from roxabi_blobs import HttpBlobStore
from roxabi_contracts.blob_ref import BlobRef as ContractsBlobRef

_INSTANCE: HttpBlobStore | None = None
_LOCK = threading.Lock()

_PENDING_STORE_KEY = "__pending__"


class BlobstoreConfigError(RuntimeError):
    """Blobstore env/config is missing or invalid."""


class BlobRefValidationError(BlobstoreConfigError):
    """``blob_ref`` failed contract validation."""


def _resolve_bearer_token() -> str:
    path = os.environ.get("BLOBSTORE_BEARER_TOKEN_PATH", "").strip()
    if path:
        try:
            token = Path(path).read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise BlobstoreConfigError(
                f"BLOBSTORE_BEARER_TOKEN_PATH file not found: {path}"
            ) from exc
        except OSError as exc:
            raise BlobstoreConfigError(
                f"BLOBSTORE_BEARER_TOKEN_PATH unreadable: {path}: {exc}"
            ) from exc
        if not token:
            raise BlobstoreConfigError(f"BLOBSTORE_BEARER_TOKEN_PATH is empty: {path}")
        return token
    try:
        token = os.environ["BLOBSTORE_BEARER_TOKEN"].strip()
    except KeyError as exc:
        raise BlobstoreConfigError(
            "required env var not set: BLOBSTORE_BEARER_TOKEN (or set BLOBSTORE_BEARER_TOKEN_PATH)"
        ) from exc
    if not token:
        raise BlobstoreConfigError("BLOBSTORE_BEARER_TOKEN is empty")
    return token


def apply_factory_blobstore_env_aliases() -> None:
    """Map Factory deploy env (``FACTORY_BLOBSTORE_*``) to ADR-068 ``BLOBSTORE_*`` names."""
    if not os.environ.get("BLOBSTORE_URL", "").strip():
        url = os.environ.get("FACTORY_BLOBSTORE_URL", "").strip()
        if url:
            os.environ["BLOBSTORE_URL"] = url
    if not os.environ.get("BLOBSTORE_BEARER_TOKEN_PATH", "").strip():
        path = os.environ.get("FACTORY_BLOBSTORE_TOKEN_PATH", "").strip()
        if path:
            os.environ["BLOBSTORE_BEARER_TOKEN_PATH"] = path
    if not os.environ.get("BLOBSTORE_BACKEND", "").strip():
        os.environ["BLOBSTORE_BACKEND"] = "http"


def get_blobstore() -> HttpBlobStore:
    """Lazy singleton ``HttpBlobStore`` from ``BLOBSTORE_*`` env vars."""
    global _INSTANCE
    if _INSTANCE is None:
        with _LOCK:
            if _INSTANCE is None:
                backend = os.environ.get("BLOBSTORE_BACKEND", "")
                if backend != "http":
                    raise BlobstoreConfigError(
                        f"BLOBSTORE_BACKEND must be 'http' for satellites (got {backend!r}); "
                        "FS-direct backend forbidden per ADR-068"
                    )
                try:
                    url = os.environ["BLOBSTORE_URL"]
                except KeyError as e:
                    raise BlobstoreConfigError(f"required env var not set: {e.args[0]}") from e
                token = _resolve_bearer_token()
                _INSTANCE = HttpBlobStore(base_url=url, token=token)
    return _INSTANCE


def reset_blobstore_for_tests() -> None:
    global _INSTANCE
    with _LOCK:
        _INSTANCE = None


def blob_ref_to_contract(ref: Any) -> ContractsBlobRef:
    store_key = getattr(ref, "store_key", "")
    if store_key == _PENDING_STORE_KEY:
        raise BlobRefValidationError(f"Pending store_key not allowed: {store_key!r}")
    try:
        return ContractsBlobRef.from_store_ref(ref)
    except ValidationError as e:
        raise BlobRefValidationError(str(e)) from e