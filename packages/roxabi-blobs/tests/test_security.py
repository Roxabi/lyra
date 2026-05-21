"""Security tests — path traversal containment in `get()` (consensus B1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from roxabi_blobs import BlobNotFoundError, FsBlobStore


class TestPathTraversal:
    """`get(store_key)` must reject any path that does not resolve under `self.root`."""

    @pytest.mark.parametrize(
        "evil_key",
        [
            "/etc/passwd",
            "/etc/shadow",
            "/root/.ssh/authorized_keys",
            "/home/mickael/.lyra/auth.db",
            "../../../../../../../etc/passwd",
            "../../../etc/hostname",
        ],
    )
    async def test_absolute_or_relative_traversal_rejected(
        self, store: FsBlobStore, evil_key: str
    ) -> None:
        """Static message — caller cannot distinguish 'rejected' from 'missing'."""
        with pytest.raises(BlobNotFoundError, match="^blob not found$"):
            await store.get(evil_key)

    async def test_legit_store_key_works(self, store: FsBlobStore) -> None:
        """Containment check does not break the happy path."""
        ref = await store.put(b"payload", mime="text/plain", source="test")
        data = await store.get(ref.store_key)
        assert data == b"payload"

    async def test_traversal_error_does_not_leak_path(self, store: FsBlobStore) -> None:
        """consensus W1 — error message is static; the offending path must not leak."""
        try:
            await store.get("/etc/passwd")
        except BlobNotFoundError as e:
            assert "/etc/passwd" not in str(e)
            assert "passwd" not in str(e)
            assert str(e) == "blob not found"
        else:
            pytest.fail("expected BlobNotFoundError")

    async def test_symlink_escape_rejected(
        self, store: FsBlobStore, tmp_path: Path
    ) -> None:
        """Symlink inside root → outside file rejected; `resolve()` follows links."""
        outside = tmp_path.parent / "secret.txt"
        outside.write_bytes(b"secret")
        link = store.root / "00" / "linked"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(outside)
        with pytest.raises(BlobNotFoundError, match="^blob not found$"):
            await store.get(str(link))
