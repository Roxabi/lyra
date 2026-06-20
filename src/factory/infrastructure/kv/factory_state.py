"""Shared helpers for the ``factory-state`` JetStream KV bucket."""

from __future__ import annotations

from typing import Any

FACTORY_STATE_BUCKET = "factory-state"


async def open_or_create_kv(js: Any) -> Any:
    """Open or create the factory-state KV bucket (hub-only provisioner path)."""
    from nats.js.api import KeyValueConfig, StorageType
    from nats.js.errors import BadRequestError, BucketNotFoundError

    try:
        return await js.key_value(FACTORY_STATE_BUCKET)
    except BucketNotFoundError:
        pass

    try:
        return await js.create_key_value(
            KeyValueConfig(bucket=FACTORY_STATE_BUCKET, storage=StorageType.FILE)
        )
    except BadRequestError as exc:
        # err_code 10058 = stream name already in use — lost creation race; open it.
        if exc.err_code != 10058:
            raise
        return await js.key_value(FACTORY_STATE_BUCKET)
