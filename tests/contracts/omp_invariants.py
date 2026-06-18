"""Shared OMP contract helpers — LlmResult shape invariants (ADR-089 P2 shim)."""

from __future__ import annotations

from typing import Any

from factory.core.ports.llm import LlmResult


def llm_result_shape(result: LlmResult) -> dict[str, Any]:
    """Serialize LlmResult to a stable dict for golden comparisons."""
    we = result.worker_error
    return {
        "result": result.result,
        "error": result.error,
        "retryable": result.retryable,
        "worker_error": (
            None
            if we is None
            else {
                "code": we.code,
                "message": we.message,
                "retryable": we.retryable,
            }
        ),
    }


def assert_llm_result_invariant(result: LlmResult) -> None:
    """ADR-089 P2 shim: ok/error paths must keep worker_error, error, retryable coherent."""
    if result.ok:
        assert result.error == ""
        assert result.worker_error is None
        return

    assert result.error
    assert result.worker_error is not None
    assert result.worker_error.message == result.error
    assert result.retryable == result.worker_error.retryable