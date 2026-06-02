"""Protocol conformance tests for BlobStorePort.

Guards structural conformance of the @runtime_checkable Protocol: any class
that implements put/get/exists satisfies the Protocol at isinstance() time, and
any class missing one of those methods does not.
"""

from __future__ import annotations


class TestBlobStorePortConformance:
    def test_conforming_class_is_instance(self) -> None:
        """A class implementing put/get/exists satisfies BlobStorePort."""
        # Arrange
        from factory.core.ports.blobstore import (
            BlobStorePort,
        )  # RED — does not exist yet

        class MockBlobStore:
            async def put(  # noqa: PLR0913
                self,
                data: bytes,
                *,
                mime: str,
                source: str,
                filename: str | None = None,
                platform_ref: str | None = None,
                platform_message_id: str | None = None,
            ) -> object: ...

            async def get(self, store_key: str) -> bytes: ...

            async def exists(self, content_hash: str) -> object: ...

        # Act / Assert
        assert isinstance(MockBlobStore(), BlobStorePort) is True

    def test_non_conforming_class_missing_put_is_not_instance(self) -> None:
        """A class missing `put` does NOT satisfy BlobStorePort."""
        # Arrange
        from factory.core.ports.blobstore import (
            BlobStorePort,
        )  # RED — does not exist yet

        class NoPut:
            async def get(self, store_key: str) -> bytes: ...

            async def exists(self, content_hash: str) -> object: ...

        # Act / Assert
        assert isinstance(NoPut(), BlobStorePort) is False
