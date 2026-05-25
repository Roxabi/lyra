"""lyra.blobstore — HTTP-fronted BlobStore service."""

from lyra.blobstore.serve import build_app

__all__ = ["build_app"]
