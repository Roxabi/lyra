"""HttpTransport — sibling transport for HTTP-based LLM providers.

Skeleton: structurally satisfies _TransportLike (call + publish + open_inbox).
P4 (#1281) ships shape only; no real HTTP I/O wired. Future HTTP drivers
compose via LlmClient(pool=WorkerPoolClient(HttpTransport(...)), codec=...).

Phase 1 consensus (§B1): publish + open_inbox raise NotImplementedError —
HTTP streaming is codec-level (SSE), not transport-level.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager

from factory.transport._result import Err, Result, SanitizedError


class HttpTransport:
    async def call(
        self, subject: str, payload: bytes, *, timeout: float | None = None
    ) -> Result[bytes, SanitizedError]:
        del subject, payload, timeout
        return Err(
            SanitizedError(
                code="NOT_WIRED",
                message="HttpTransport.call is a P4 skeleton — no HTTP backend wired.",
                retryable=False,
            )
        )

    async def publish(
        self, subject: str, payload: bytes, *, reply_subject: str
    ) -> None:
        del subject, payload, reply_subject
        raise NotImplementedError(
            "HTTP transport does not provide pub/sub publish; "
            "use codec-level request/response."
        )

    def open_inbox(self) -> AbstractAsyncContextManager:
        raise NotImplementedError(
            "HTTP transport does not provide inbox-based streaming; "
            "use codec-level SSE."
        )
