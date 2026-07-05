"""NATS transport layer (Phase 1 #1278).

Public surface for the Result helper (Err/Ok/Result/SanitizedError/InboxStream).
Prefer importing from here, never factory.transport._result directly (see
.importlinter transport-result-public-surface contract).
"""

from ._result import Err, InboxStream, Ok, Result, SanitizedError

__all__ = ["Err", "InboxStream", "Ok", "Result", "SanitizedError"]
