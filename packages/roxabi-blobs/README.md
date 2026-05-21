# roxabi-blobs

Content-addressed `BlobStore` for the Roxabi plugin ecosystem.

Backs binary payloads (audio, images, documents, video) with a Flat-FS + SQLite manifest behind a uniform `BlobStore` Protocol. Sha-256 keyed; per-source provenance preserved via `blob_refs` (many-to-one over `blobs`). Single-host (M₁) for v1; FS impl is swappable for MinIO/S3 behind the same Protocol when multi-host arrives.

Implements [ADR-067](../../docs/architecture/adr/067-blobstore-abstraction-flat-fs-content-addressed.mdx).

## Public API

```python
from roxabi_blobs import BlobStore, BlobRef, BlobNotFoundError, BlobWriteError

async with FsBlobStore(root=Path("/data/lyra/blobs")) as store:
    ref = await store.put(data, mime="audio/ogg", source="telegram")
    bytes_back = await store.get(ref.store_key)
    found = await store.exists(ref.content_hash)
    await store.delete(blob_ref_id=…)  # designed; not called in v1 paths
```

## Layout

```
<root>/
├── index.sqlite       # WAL — blobs + blob_refs tables
└── <sha[:2]>/<sha>    # sharded by 2-char prefix (256 dirs)
```
