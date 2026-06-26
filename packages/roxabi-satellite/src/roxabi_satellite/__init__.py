"""roxabi_satellite — shared NATS satellite plumbing for Roxabi GPU workers."""

from roxabi_satellite.blobs import (
    BlobRefValidationError,
    BlobstoreConfigError,
    apply_factory_blobstore_env_aliases,
    blob_ref_to_contract,
    get_blobstore,
    reset_blobstore_for_tests,
)
from roxabi_satellite.envelope import coerce_envelope_fields, work_fields_from_request
from roxabi_satellite.errors import resolve_worker_error
from roxabi_satellite.tokens import validate_nats_token, validate_nats_single_token

__all__ = [
    "BlobRefValidationError",
    "BlobstoreConfigError",
    "apply_factory_blobstore_env_aliases",
    "blob_ref_to_contract",
    "coerce_envelope_fields",
    "get_blobstore",
    "reset_blobstore_for_tests",
    "resolve_worker_error",
    "validate_nats_single_token",
    "validate_nats_token",
    "work_fields_from_request",
]