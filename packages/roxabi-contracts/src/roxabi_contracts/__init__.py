"""roxabi_contracts — shared Pydantic schemas for Lyra cross-project NATS contracts.

See docs/architecture/adr/049-roxabi-contracts-shared-schema-package.mdx.

Public API: only the names in ``__all__`` are part of the stable external
contract. v0.1.0 ships ``ContractEnvelope`` and ``CONTRACT_VERSION``;
per-domain submodules (voice, image, memory, llm) arrive in later tags.
"""

from .audit import SecurityEvent
from .envelope import CONTRACT_VERSION, ContractEnvelope

__all__ = ["CONTRACT_VERSION", "ContractEnvelope", "SecurityEvent"]
