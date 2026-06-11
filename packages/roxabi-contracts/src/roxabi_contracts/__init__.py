"""roxabi_contracts — shared Pydantic schemas for Lyra cross-project NATS contracts.

See docs/architecture/adr/049-roxabi-contracts-shared-schema-package.mdx.

Public API: only the names in ``__all__`` are part of the stable external
contract. v0.1.0 ships ``ContractEnvelope`` and ``CONTRACT_VERSION``;
per-domain submodules (voice, image, memory, llm) arrive in later tags.
"""

from importlib.metadata import PackageNotFoundError, version

from ._nats_utils import validate_subject_segment
from .audit import SecurityEvent
from .blob_errors import BlobNotFoundError, BlobStoreServerError
from .blob_ref import BlobRef
from .envelope import CONTRACT_VERSION, ContractEnvelope, WorkEnvelope, new_job_id

try:
    __version__: str = version("roxabi-contracts")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = [
    "BlobNotFoundError",
    "BlobRef",
    "BlobStoreServerError",
    "CONTRACT_VERSION",
    "ContractEnvelope",
    "SecurityEvent",
    "WorkEnvelope",
    "__version__",
    "new_job_id",
    "validate_subject_segment",
]
