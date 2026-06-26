"""Spike #1813 V3 — omp_rpc session API surface + persistence-mode test.

Answers: does omp support DURABLE sessions (session_id -> reloadable on any
process)? That is the precondition for Model B (and for Model A surviving
eviction). No LLM call needed for the signature dump.
THROWAWAY.
"""

# pyright: reportMissingImports=false
from __future__ import annotations

import inspect
import os
from pathlib import Path


def bridge() -> None:
    if os.environ.get("LITELLM_API_KEY"):
        return
    kf = os.environ.get("LITELLM_API_KEY_FILE")
    if kf:
        os.environ["LITELLM_API_KEY"] = Path(kf).read_text().strip()


def main() -> None:
    bridge()
    import omp_rpc
    from omp_rpc import RpcClient

    print("=== omp_rpc top-level names ===")
    print([n for n in dir(omp_rpc) if not n.startswith("_")])

    print("\n=== RpcClient.__init__ signature ===")
    print(inspect.signature(RpcClient.__init__))

    print("\n=== session-related methods (signature + 1-line doc) ===")
    for name in sorted(dir(RpcClient)):
        if name.startswith("_"):
            continue
        if not any(
            k in name.lower() for k in ("session", "save", "load", "resume", "state")
        ):
            continue
        m = getattr(RpcClient, name)
        if not callable(m):
            continue
        try:
            sig = str(inspect.signature(m))
        except (ValueError, TypeError):
            sig = "(?)"
        doc = (inspect.getdoc(m) or "").splitlines()
        print(f"  {name}{sig}")
        if doc:
            print(f"      {doc[0]}")

    # Persistence-mode probe: construct WITHOUT no_session, look for a session dir.
    print("\n=== persistence-mode probe (no_session=False) ===")
    try:
        c = RpcClient(
            executable=os.environ.get("OMP_BIN", "/opt/omp/omp"),
            provider="litellm",
            model="grok-4-fast",
            no_session=False,
        )
        c.start()
        try:
            st = c.get_state()
            print(f"  session_id={getattr(st, 'session_id', '?')}")
            print(f"  session_file={getattr(st, 'session_file', '?')}")
        finally:
            c.stop()
    except Exception as exc:  # noqa: BLE001
        print(f"  no_session=False FAILED: {type(exc).__name__}: {exc}")

    # Where does omp keep sessions on disk, if anywhere?
    print("\n=== on-disk session dirs ===")
    for base in (
        "/home/factory/.omp",
        os.environ.get("HOME", "") + "/.omp",
        "/home/factory/.config/omp-pi",
    ):
        p = Path(base)
        if p.exists():
            hits = sorted(str(x) for x in p.glob("**/*session*"))[:10]
            print(f"  {p}: {hits or 'no *session* entries'}")
        else:
            print(f"  {p}: (absent)")


if __name__ == "__main__":
    main()
