"""factory.blobstore — HTTP-fronted BlobStore service."""

from factory.blobstore.serve import build_app

__all__ = ["build_app"]
