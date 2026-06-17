"""Spike #1813 V3 — omp_rpc API introspection (one cheap turn).

Learns WHERE the session id/path lives and WHEN it is populated, so the
switch_session probe can capture the right token at the right time.
THROWAWAY. Run inside factory-omp with OMP_MODEL=grok-4-fast.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import os
from pathlib import Path

OMP_BIN = os.environ.get("OMP_BIN", "/opt/omp/omp")


def bridge() -> None:
    if os.environ.get("LITELLM_API_KEY"):
        return
    kf = os.environ.get("LITELLM_API_KEY_FILE")
    if kf:
        os.environ["LITELLM_API_KEY"] = Path(kf).read_text().strip()


def attrs(obj: object) -> dict:
    out = {}
    for a in dir(obj):
        if a.startswith("_"):
            continue
        try:
            v = getattr(obj, a)
        except Exception as e:  # noqa: BLE001
            v = f"<err {type(e).__name__}>"
        if callable(v):
            continue
        out[a] = repr(v)[:120]
    return out


def main() -> None:
    bridge()
    from omp_rpc import RpcClient

    c = RpcClient(
        executable=OMP_BIN, provider="litellm", model="grok-4-fast", no_session=True
    )
    c.start()
    try:
        c.new_session()
        st1 = c.get_state()
        print("=== state AFTER new_session (before any turn) ===")
        for k, v in attrs(st1).items():
            print(f"  {k} = {v}")

        turn = c.prompt_and_wait("Remember codeword KIWI9. Reply with just OK.")
        print("\n=== turn object AFTER prompt_and_wait ===")
        for k, v in attrs(turn).items():
            print(f"  {k} = {v}")

        st2 = c.get_state()
        print("\n=== state AFTER first turn ===")
        for k, v in attrs(st2).items():
            print(f"  {k} = {v}")

        # Does the session dir on disk now hold a .jsonl?
        for base in ("/home/factory/.omp", os.environ.get("HOME", "") + "/.omp"):
            p = Path(base) / "sessions"
            if p.is_dir():
                files = sorted(str(x) for x in p.glob("**/*"))[:10]
                print(f"\n=== {p} ({len(files)} entries) ===")
                for f in files:
                    print(f"  {f}")
    finally:
        c.stop()


if __name__ == "__main__":
    main()
