"""
Spike #1813 V3 — omp session/process decoupling probe (no_session=False).

Settles Model A (warm process pinned per conversation) vs Model B (flat pool of
interchangeable workers, durable session resumed per turn).

KEY API FACTS (learned via omp_api_dump.py / omp_introspect.py on M1):
  - RpcClient(no_session=True)  -> session in-memory only, session_file=None,
    NO durable session, switch_session has nothing to resume. (This is what the
    worker + OmpPool hardcode today -> memory survives ONLY via warm retention.)
  - RpcClient(no_session=False) -> each session persists a .jsonl under
    PI_CODING_AGENT_DIR/sessions/--app--/<ts>_<session_id>.jsonl;
    get_state().session_file is that path; switch_session(path) resumes it on
    ANY process. <- precondition for Model B AND for Model A surviving eviction.
  - request_timeout defaults to 30s -> grok-4 (full) times out; grok-4-fast fits.

THROWAWAY. Run INSIDE factory-omp on M1 with OMP_MODEL=grok-4-fast.
"""

# pyright: reportMissingImports=false
from __future__ import annotations

import os
import time
from pathlib import Path

OMP_BIN = os.environ.get("OMP_BIN", "/opt/omp/omp")
OMP_MODEL = os.environ.get("OMP_MODEL", "grok-4-fast")


def _bridge() -> str:
    if os.environ.get("LITELLM_API_KEY"):
        return "LITELLM_API_KEY inherited"
    kf = os.environ.get("LITELLM_API_KEY_FILE")
    if not kf:
        return "WARN: no LITELLM_API_KEY_FILE"
    os.environ["LITELLM_API_KEY"] = Path(kf).read_text().strip()
    return f"bridged LITELLM_API_KEY from {kf}"


from omp_rpc import RpcClient  # noqa: E402


def _client() -> object:
    # no_session=False -> durable .jsonl session (the whole point of this probe).
    return RpcClient(
        executable=OMP_BIN, provider="litellm", model=OMP_MODEL, no_session=False
    )


def _say(c: object, prompt: str) -> str:
    turn = c.prompt_and_wait(prompt)  # type: ignore[attr-defined]
    return (getattr(turn, "assistant_text", "") or "").strip()


def _file(c: object) -> str:
    return getattr(c.get_state(), "session_file", "") or ""  # type: ignore[attr-defined]


def _has(text: str, word: str) -> bool:
    return word.lower() in text.lower()


# -- Probe A: isolation + live re-switch on ONE process --------------------
def probe_a() -> tuple[bool, str]:
    out: list[str] = []
    try:
        c = _client()
        c.start()  # type: ignore[attr-defined]
        try:
            file_a = _file(c)
            _say(c, "Remember this codeword: BANANA42. Reply with just OK.")
            out.append(f"A={Path(file_a).name}")

            c.new_session()  # type: ignore[attr-defined]
            file_b = _file(c)
            out.append(f"B={Path(file_b).name}")
            if not file_b or file_b == file_a:
                return False, "new_session did not mint a distinct B | " + " ".join(out)
            iso = not _has(
                _say(c, "What codeword did I give you? If none, reply NONE."),
                "BANANA42",
            )

            t0 = time.monotonic()
            c.switch_session(file_a)  # type: ignore[attr-defined]
            t_sw = (time.monotonic() - t0) * 1000
            mem = _has(
                _say(c, "What codeword did I give you earlier? Reply just the word."),
                "BANANA42",
            )
            out.append(
                f"isolated={iso} recalled_after_live_switch={mem} switch={t_sw:.0f}ms"
            )
            return (iso and mem), " | ".join(out)
        finally:
            c.stop()  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc} | {' '.join(out)}"


# -- Probe B: cross-PROCESS resume by file (the Model B proof) --------------
def probe_b() -> tuple[bool, str]:
    out: list[str] = []
    try:
        c1 = _client()
        c1.start()  # type: ignore[attr-defined]
        try:
            file_a = _file(c1)
            _say(c1, "Remember this codeword: CHERRY7. Reply with just OK.")
            out.append(f"writer={Path(file_a).name}")
        finally:
            c1.stop()  # type: ignore[attr-defined]

        c2 = _client()
        t0 = time.monotonic()
        c2.start()  # type: ignore[attr-defined]
        t_start = (time.monotonic() - t0) * 1000
        try:
            t1 = time.monotonic()
            c2.switch_session(file_a)  # type: ignore[attr-defined]
            t_sw = (time.monotonic() - t1) * 1000
            recalled = _has(
                _say(c2, "What codeword did I give you earlier? Reply just the word."),
                "CHERRY7",
            )
            out.append(
                f"recalled={recalled} cold_start={t_start:.0f}ms switch={t_sw:.0f}ms"
            )
            return recalled, " | ".join(out)
        finally:
            c2.stop()  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc} | {' '.join(out)}"


# -- Probe C: timing — overhead ADDED on top of the unavoidable LLM call ----
def probe_c() -> tuple[bool, str]:
    out: list[str] = []
    try:
        c = _client()
        t0 = time.monotonic()
        c.start()  # type: ignore[attr-defined]
        out.append(f"start={(time.monotonic() - t0) * 1000:.0f}ms")
        try:
            file_a = _file(c)
            t1 = time.monotonic()
            _say(c, "Say hi in one word.")
            out.append(f"prompt_LLM={(time.monotonic() - t1) * 1000:.0f}ms")

            c.new_session()  # type: ignore[attr-defined]
            _say(c, "Say bye in one word.")
            t2 = time.monotonic()
            c.switch_session(file_a)  # type: ignore[attr-defined]
            out.append(f"switch_session={(time.monotonic() - t2) * 1000:.0f}ms")
            return True, " | ".join(out)
        finally:
            c.stop()  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc} | {' '.join(out)}"


def main() -> None:
    print("\n=== Spike #1813 V3 — omp session decoupling (no_session=False) ===")
    print(f"bin={OMP_BIN} model={OMP_MODEL} | {_bridge()}")
    print("-" * 78)
    ok_all = True
    for name, fn in [
        ("A isolation + live re-switch", probe_a),
        ("B cross-process resume (Model B proof)", probe_b),
        ("C switch/start timing", probe_c),
    ]:
        ok, details = fn()
        ok_all = ok_all and ok
        print(f"[{'PASS' if ok else 'FAIL'}] {name}\n       {details}")
    print("-" * 78)
    print(f"OVERALL: {'PASS' if ok_all else 'FAIL'}\n")


if __name__ == "__main__":
    main()
