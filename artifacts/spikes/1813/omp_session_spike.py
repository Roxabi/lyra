"""
Spike #1813 — omp_rpc session continuity / resume / concurrency.

THROWAWAY — NOT production code. No import factory. Extends the #1807 PoC
(artifacts/spikes/1807/omp_rpc_poc.py) to answer the three questions #1813 needs
settled BEFORE the spec is approved:

  P0  wiring        construct + start + new_session → session_id + session_file populated?
  P1  continue      turn1 sets a fact, turn2 (SAME client/session) recalls it?
  P2  resume        capture session_file, stop client, NEW client switch_session(path),
                    recall the fact? (= clipool --resume analogue; tests persistence)
  P3  concurrency   TWO clients (two omp subprocesses), parallel prompts, each recalls
                    ITS OWN secret and does NOT see the other's (no cross-session bleed)
                    — the "one per agent" model clipool already supports.

Env (all overridable; defaults target the factory-omp prod wiring):
    OMP_BIN                 default: /opt/omp/omp   (M1 container) | /tmp/omp_spike/omp (M2)
    OMP_PROVIDER            default: litellm
    OMP_MODEL               default: grok-4-fast    (must be in models.yml catalog)
    OMP_SESSION_DIR         default: /tmp/omp_spike_1813   (where session files land)
    PI_CODING_AGENT_DIR     must point at a dir containing models.yml (proxy baseUrl)
    LITELLM_API_KEY         proxy bearer (bridge from the *_FILE secret before running)

Usage:
    python omp_session_spike.py            # run P0..P3 in order, summary table
"""

# omp_rpc is an external alpha package — live-run box only.
# pyright: reportMissingImports=false
from __future__ import annotations

import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

OMP_BIN = os.environ.get("OMP_BIN", "/opt/omp/omp")
OMP_PROVIDER = os.environ.get("OMP_PROVIDER", "litellm")
OMP_MODEL = os.environ.get("OMP_MODEL", "grok-4-fast")
OMP_SESSION_DIR = Path(os.environ.get("OMP_SESSION_DIR", "/tmp/omp_spike_1813"))

try:
    from omp_rpc import MessageUpdateEvent, RpcClient
except ImportError as exc:  # pragma: no cover — live-run box only
    print(f"[FATAL] omp_rpc not importable: {exc}", file=sys.stderr)
    sys.exit(2)


def _collect_text(client: RpcClient, sink: list[str]) -> None:
    """Wire on_message_update → append text_delta chunks into sink."""

    def on_msg_update(evt: MessageUpdateEvent) -> None:
        asst = evt.assistant_message_event
        if asst and asst.get("type") == "text_delta":
            sink.append(str(asst.get("delta", "")))

    client.on_message_update(on_msg_update)


def _turn_text(turn: Any, sink: list[str]) -> str:
    """Best-effort assistant text: streamed chunks, else turn accessor."""
    streamed = "".join(sink).strip()
    if streamed:
        return streamed
    try:
        return str(turn.require_assistant_text()).strip()
    except Exception:  # noqa: BLE001 — fall back to empty
        return ""


def _new_client(**overrides: Any) -> RpcClient:
    kwargs: dict[str, Any] = dict(
        executable=OMP_BIN,
        provider=OMP_PROVIDER,
        model=OMP_MODEL,
        session_dir=str(OMP_SESSION_DIR),
    )
    kwargs.update(overrides)
    return RpcClient(**kwargs)


# ---------------------------------------------------------------------------
# P0 — wiring: can we open a session and does it have an id + a file path?
# ---------------------------------------------------------------------------
def p0_wiring() -> tuple[bool, str]:
    try:
        with _new_client() as c:
            c.new_session()
            st = c.get_state()
            sid = getattr(st, "session_id", None)
            sfile = getattr(st, "session_file", None)
        ok = bool(sid)
        return ok, f"session_id={sid!r} session_file={sfile!r}"
    except Exception as exc:  # noqa: BLE001
        return False, f"exception: {type(exc).__name__}: {exc}\n{traceback.format_exc()}"


# ---------------------------------------------------------------------------
# P1 — continue: two turns on the SAME client recall a fact
# ---------------------------------------------------------------------------
def p1_continue() -> tuple[bool, str]:
    secret = "ZORGLUB-7"
    try:
        with _new_client() as c:
            sink: list[str] = []
            _collect_text(c, sink)
            c.new_session()
            sid = c.get_state().session_id

            sink.clear()
            c.prompt_and_wait(
                f"Remember this codeword for later: {secret}. "
                "Reply with just 'ok'."
            )
            sink.clear()
            turn2 = c.prompt_and_wait(
                "What was the codeword I asked you to remember? "
                "Reply with only the codeword."
            )
            answer = _turn_text(turn2, sink)
        ok = secret in answer.upper()
        return ok, f"session_id={sid!r} turn2_answer={answer!r} (expect {secret})"
    except Exception as exc:  # noqa: BLE001
        return False, f"exception: {type(exc).__name__}: {exc}\n{traceback.format_exc()}"


# ---------------------------------------------------------------------------
# P2 — resume: capture session_file, close client, NEW client switch_session()
# ---------------------------------------------------------------------------
def p2_resume() -> tuple[bool, str]:
    secret = "QUETZAL-42"
    try:
        # client A: set the fact, capture the session file path
        with _new_client() as a:
            a.new_session()
            sid_a = a.get_state().session_id
            a.prompt_and_wait(
                f"Remember this codeword for later: {secret}. Reply with just 'ok'."
            )
            sfile = getattr(a.get_state(), "session_file", None)
        if not sfile:
            return False, f"session_file is empty after turn (sid={sid_a!r}) — cannot resume"

        # client B: fresh process, resume A's session by file path
        with _new_client() as b:
            sink: list[str] = []
            _collect_text(b, sink)
            b.switch_session(sfile)
            sid_b = b.get_state().session_id
            sink.clear()
            turn = b.prompt_and_wait(
                "What was the codeword I asked you to remember? "
                "Reply with only the codeword."
            )
            answer = _turn_text(turn, sink)
        ok = secret in answer.upper()
        return ok, (
            f"sfile={sfile!r} sid_a={sid_a!r} sid_b={sid_b!r} "
            f"resumed_answer={answer!r} (expect {secret})"
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"exception: {type(exc).__name__}: {exc}\n{traceback.format_exc()}"


# ---------------------------------------------------------------------------
# P3 — concurrency: two clients, parallel turns, no cross-session bleed
# ---------------------------------------------------------------------------
def p3_concurrency() -> tuple[bool, str]:
    cases = [("ALPHA-1", "session_a"), ("BRAVO-2", "session_b")]
    results: dict[str, str] = {}
    errors: dict[str, str] = {}

    def run_one(secret: str, label: str) -> None:
        try:
            with _new_client() as c:
                sink: list[str] = []
                _collect_text(c, sink)
                c.new_session()
                c.prompt_and_wait(
                    f"Remember this codeword: {secret}. Reply with just 'ok'."
                )
                sink.clear()
                turn = c.prompt_and_wait(
                    "What was the codeword? Reply with only the codeword."
                )
                results[label] = _turn_text(turn, sink)
        except Exception as exc:  # noqa: BLE001
            errors[label] = f"{type(exc).__name__}: {exc}"

    threads = [
        threading.Thread(target=run_one, args=(secret, label))
        for secret, label in cases
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    if errors:
        return False, f"errors={errors} results={results}"
    a_ans = results.get("session_a", "").upper()
    b_ans = results.get("session_b", "").upper()
    # each recalls its own secret AND does not leak the other's
    a_ok = "ALPHA-1" in a_ans and "BRAVO-2" not in a_ans
    b_ok = "BRAVO-2" in b_ans and "ALPHA-1" not in b_ans
    ok = a_ok and b_ok
    return ok, f"session_a={results.get('session_a')!r} session_b={results.get('session_b')!r}"


def main() -> None:
    OMP_SESSION_DIR.mkdir(parents=True, exist_ok=True)
    print(
        f"\nConfig: bin={OMP_BIN!r} provider={OMP_PROVIDER!r} model={OMP_MODEL!r} "
        f"session_dir={str(OMP_SESSION_DIR)!r}"
    )
    print(f"  PI_CODING_AGENT_DIR={os.environ.get('PI_CODING_AGENT_DIR')!r}")
    print(f"  LITELLM_API_KEY set={'yes' if os.environ.get('LITELLM_API_KEY') else 'NO'}\n")

    probes = [
        ("P0_wiring", p0_wiring),
        ("P1_continue", p1_continue),
        ("P2_resume", p2_resume),
        ("P3_concurrency", p3_concurrency),
    ]
    print(f"{'PROBE':<18}  {'RESULT':<6}  DETAILS")
    print("-" * 90)
    all_pass = True
    for name, fn in probes:
        ok, details = fn()
        if not ok:
            all_pass = False
        print(f"{name:<18}  {'PASS' if ok else 'FAIL':<6}  {details}")
    print("-" * 90)
    print(f"{'OVERALL':<18}  {'PASS' if all_pass else 'FAIL'}\n")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
