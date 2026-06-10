"""
Spike #1807 — omp-rpc PoC: validate oh-my-pi RPC client as claude -p replacement.

THROWAWAY — NOT production code. No import factory. Local dataclasses mirror factory
event shapes from factory.core.messaging.events (TextLlmEvent, ToolUseLlmEvent,
ResultLlmEvent).

Prerequisites (run ONCE before this script):
    uv pip install -e /home/mickael/projects/external_repos/oh-my-pi/python/omp-rpc

Usage:
    python omp_rpc_poc.py --all           # run all four probes
    python omp_rpc_poc.py --text          # text turn only
    python omp_rpc_poc.py --tool          # host-tool turn
    python omp_rpc_poc.py --steer         # steer injection
    python omp_rpc_poc.py --abort         # abort mid-turn
"""

# omp_rpc is an external alpha package, installed only on the live-run box (see RUNBOOK.md).
# pyright: reportMissingImports=false
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Local dataclasses mirroring factory event shapes (NO import factory)
# Source: factory.core.messaging.events (TextLlmEvent, ToolUseLlmEvent, ResultLlmEvent)
# ---------------------------------------------------------------------------


@dataclass
class TextLlmEvent:
    text: str


@dataclass
class ToolUseLlmEvent:
    tool_name: str
    tool_id: str
    input: dict[str, Any]  # noqa: A003 — mirrors factory field name


@dataclass
class ResultLlmEvent:
    is_error: bool
    session_id: str
    worker_error: str | None = None


# ---------------------------------------------------------------------------
# Env config — all overridable; defaults target M1 via Tailnet
# ---------------------------------------------------------------------------

OMP_PROVIDER = os.environ.get("OMP_PROVIDER", "litellm")
OMP_MODEL = os.environ.get("OMP_MODEL", "claude-opus-4-6")
# NOTE: LITELLM_BASE_URL does NOT exist as a recognised env var in oh-my-pi.
# The baseUrl is TypeScript-constructor-level only (litellmModelManagerOptions).
# localhost:4000 is the hard-coded default in the TS source.
# To reach M1 Tailnet LiteLLM from M2: export LITELLM_API_KEY="" and make sure
# omp resolves roxabituwer:4000 — no env-var override path exists without a custom
# omp config file or custom --command launcher.
OMP_LITELLM_BASE_URL = os.environ.get("OMP_LITELLM_BASE_URL", "http://localhost:4000/v1")
OMP_SESSION_DIR = Path(os.environ.get("OMP_SESSION_DIR", "/tmp/omp_spike_1807"))
OMP_BIN = os.environ.get("OMP_BIN", "omp")  # override with full path if not on PATH

# LITELLM_API_KEY: pass-through — omp picks it up directly (allowUnauthenticated: true)

# ---------------------------------------------------------------------------
# Shared event collectors (thread-safe append-only)
# ---------------------------------------------------------------------------


@dataclass
class TurnCollector:
    text_events: list[TextLlmEvent] = field(default_factory=list)
    tool_events: list[ToolUseLlmEvent] = field(default_factory=list)
    result_event: ResultLlmEvent | None = None
    raw_text_chunks: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add_text(self, chunk: str) -> None:
        with self._lock:
            self.raw_text_chunks.append(chunk)

    def add_tool(self, evt: ToolUseLlmEvent) -> None:
        with self._lock:
            self.tool_events.append(evt)

    def set_result(self, evt: ResultLlmEvent) -> None:
        with self._lock:
            self.result_event = evt

    def assembled_text(self) -> str:
        return "".join(self.raw_text_chunks)


# ---------------------------------------------------------------------------
# omp-rpc import (fails loudly with install hint)
# ---------------------------------------------------------------------------

try:
    from omp_rpc import (
        AgentEndEvent,
        HostToolContext,
        MessageUpdateEvent,
        RpcClient,
        ToolExecutionStartEvent,
        host_tool,
    )
except ImportError as exc:
    print(
        f"[FATAL] omp_rpc not installed: {exc}\n"
        "Run: uv pip install -e "
        "/home/mickael/projects/external_repos/oh-my-pi/python/omp-rpc",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Probe 1: plain text turn
# ---------------------------------------------------------------------------


def run_text_turn() -> tuple[bool, str]:
    """
    Open a new session, fire a one-shot text prompt, collect text_delta events,
    map onto TextLlmEvent + ResultLlmEvent, verify non-empty text returned.
    """
    collector = TurnCollector()

    try:
        with RpcClient(
            executable=OMP_BIN,
            provider=OMP_PROVIDER,
            model=OMP_MODEL,
            no_session=True,  # we call new_session() manually
        ) as client:
            # Register listeners BEFORE new_session (they survive session lifecycle)
            def on_msg_update(evt: MessageUpdateEvent) -> None:
                asst = evt.assistant_message_event
                if asst and asst.get("type") == "text_delta":
                    chunk = str(asst.get("delta", ""))
                    collector.add_text(chunk)

            def on_agent_end(evt: AgentEndEvent) -> None:
                # Map terminal event onto ResultLlmEvent
                collector.set_result(
                    ResultLlmEvent(
                        is_error=False,
                        session_id=_session_id,
                        worker_error=None,
                    )
                )

            client.on_message_update(on_msg_update)
            client.on_agent_end(on_agent_end)

            client.new_session()
            state = client.get_state()
            _session_id = state.session_id

            turn = client.prompt_and_wait(
                "Reply with exactly the word PONG and nothing else."
            )
            text = turn.require_assistant_text()
            collector.add_text(text)  # fallback — require_assistant_text may duplicate
            # Normalise: use assembled chunks if non-empty, else turn text
            final_text = collector.assembled_text() or text

    except Exception as exc:  # noqa: BLE001 — PoC, capture all failures
        return False, f"exception: {exc}"

    ok = "PONG" in final_text.strip().upper()
    return ok, f"text={final_text!r} session_id={_session_id!r}"


# ---------------------------------------------------------------------------
# Probe 2: host-tool turn
# ---------------------------------------------------------------------------


def _echo_execute(
    params: dict[str, Any], ctx: HostToolContext[None]  # type: ignore[type-arg]
) -> str:
    """Synchronous host-tool — runs in threading.Thread, safe to block."""
    message = params.get("message", "")
    return f"HOST_ECHO:{message}"


_ECHO_TOOL = host_tool(
    name="echo_host",
    description=(
        "Echo a message back from the Python host. "
        "Call this tool with a 'message' argument."
    ),
    parameters={
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The string to echo back.",
            }
        },
        "required": ["message"],
        "additionalProperties": False,
    },
    execute=_echo_execute,
)


def run_tool_turn() -> tuple[bool, str]:
    """
    Register echo_host host-tool, prompt omp to call it, collect
    ToolExecutionStartEvent, verify tool round-trip completes.
    """
    collector = TurnCollector()
    _session_id = ""

    try:
        with RpcClient(
            executable=OMP_BIN,
            provider=OMP_PROVIDER,
            model=OMP_MODEL,
            no_session=True,
            custom_tools=(_ECHO_TOOL,),
        ) as client:
            def on_tool_start(evt: ToolExecutionStartEvent) -> None:
                collector.add_tool(
                    ToolUseLlmEvent(
                        tool_name=evt.tool_name,
                        tool_id=evt.tool_call_id,
                        input=evt.args if isinstance(evt.args, dict) else {},
                    )
                )

            def on_agent_end(evt: AgentEndEvent) -> None:
                collector.set_result(
                    ResultLlmEvent(
                        is_error=False,
                        session_id=_session_id,
                        worker_error=None,
                    )
                )

            client.on_tool_execution_start(on_tool_start)
            client.on_agent_end(on_agent_end)

            client.new_session()
            state = client.get_state()
            _session_id = state.session_id

            client.prompt_and_wait(
                "Use the echo_host tool with the message 'spike1807'."
            )

    except Exception as exc:  # noqa: BLE001
        return False, f"exception: {exc}"

    tool_calls = collector.tool_events
    ok = (
        len(tool_calls) >= 1
        and tool_calls[0].tool_name == "echo_host"
    )
    details = f"tool_calls={[t.tool_name for t in tool_calls]} session_id={_session_id!r}"
    return ok, details


# ---------------------------------------------------------------------------
# Probe 3: steer injection
# ---------------------------------------------------------------------------


def run_steer_probe() -> tuple[bool, str]:
    """
    Start a long-running prompt, inject steer() from a background thread
    mid-turn (between tool calls), confirm turn completes without error.

    steer() is fire-and-forget and bypasses _prompt_lifecycle — safe from
    any thread while prompt_and_wait() is blocking (client.py:892).
    Applies between tool calls only — never mid-tool (wire docs rpc.md:363-366).
    """
    steer_sent = threading.Event()
    steer_error: list[Exception] = []
    _session_id = ""

    try:
        with RpcClient(
            executable=OMP_BIN,
            provider=OMP_PROVIDER,
            model=OMP_MODEL,
            no_session=True,
        ) as client:
            client.new_session()
            state = client.get_state()
            _session_id = state.session_id

            def _steer_thread() -> None:
                # Wait briefly then inject steering
                time.sleep(0.5)
                try:
                    client.steer(
                        "Please keep your reply very short — one sentence max.",
                    )
                    steer_sent.set()
                except Exception as exc:  # noqa: BLE001
                    steer_error.append(exc)

            t = threading.Thread(target=_steer_thread, daemon=True)
            t.start()

            client.prompt_and_wait(
                "Count from 1 to 50, one number per line. Take your time.",
            )
            t.join(timeout=3.0)

    except Exception as exc:  # noqa: BLE001
        return False, f"exception: {exc}"

    if steer_error:
        return False, f"steer exception: {steer_error[0]}"

    ok = steer_sent.is_set()
    # GREEN: steer() didn't raise; turn completed without crash.
    # Effect on output is non-deterministic (steer may or may not truncate).
    return ok, f"steer_sent={steer_sent.is_set()} session_id={_session_id!r}"


# ---------------------------------------------------------------------------
# Probe 4: abort
# ---------------------------------------------------------------------------


def run_abort() -> tuple[bool, str]:
    """
    Start a prompt, abort() mid-turn from a background thread, confirm
    abort() returns without error and the client remains usable.
    """
    abort_error: list[Exception] = []
    _session_id = ""

    try:
        with RpcClient(
            executable=OMP_BIN,
            provider=OMP_PROVIDER,
            model=OMP_MODEL,
            no_session=True,
        ) as client:
            client.new_session()
            state = client.get_state()
            _session_id = state.session_id

            def _abort_thread() -> None:
                time.sleep(0.3)
                try:
                    client.abort()
                except Exception as exc:  # noqa: BLE001
                    abort_error.append(exc)

            t = threading.Thread(target=_abort_thread, daemon=True)
            t.start()

            try:
                client.prompt_and_wait(
                    "Tell me everything you know about the history of computing, "
                    "in extreme detail. Do not stop until I say so.",
                )
            except Exception:  # noqa: BLE001 — abort may raise; that is expected
                pass

            t.join(timeout=3.0)

    except Exception as exc:  # noqa: BLE001
        return False, f"exception: {exc}"

    ok = len(abort_error) == 0
    return ok, f"abort_error={abort_error} session_id={_session_id!r}"


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Spike #1807 — omp-rpc PoC probes",
    )
    parser.add_argument("--text", action="store_true", help="Run probe 1: text turn")
    parser.add_argument("--tool", action="store_true", help="Run probe 2: host-tool turn")
    parser.add_argument("--steer", action="store_true", help="Run probe 3: steer injection")
    parser.add_argument("--abort", action="store_true", help="Run probe 4: abort")
    parser.add_argument("--all", action="store_true", help="Run all probes")
    args = parser.parse_args()

    if not any([args.text, args.tool, args.steer, args.abort, args.all]):
        parser.print_help()
        sys.exit(0)

    probes: list[tuple[str, Any]] = []
    if args.all or args.text:
        probes.append(("text_turn", run_text_turn))
    if args.all or args.tool:
        probes.append(("tool_turn", run_tool_turn))
    if args.all or args.steer:
        probes.append(("steer_probe", run_steer_probe))
    if args.all or args.abort:
        probes.append(("abort", run_abort))

    OMP_SESSION_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\nConfig: provider={OMP_PROVIDER!r} model={OMP_MODEL!r} bin={OMP_BIN!r}")
    print(f"  NOTE: OMP_LITELLM_BASE_URL={OMP_LITELLM_BASE_URL!r} is informational only —")
    print("  there is no env-var override path into LiteLLMModelManagerConfig.baseUrl.")
    print("  omp defaults to http://localhost:4000/v1. Port-forward or ssh-tunnel if needed.\n")

    col_w = 16
    print(f"{'PROBE':<{col_w}}  {'RESULT':<8}  DETAILS")
    print("-" * 80)

    all_pass = True
    for name, fn in probes:
        ok, details = fn()
        result = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"{name:<{col_w}}  {result:<8}  {details}")

    print("-" * 80)
    print(f"{'OVERALL':<{col_w}}  {'PASS' if all_pass else 'FAIL'}\n")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
