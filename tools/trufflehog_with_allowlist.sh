#!/usr/bin/env bash
# TruffleHog scan with allowlist (security/trufflehog-allowlist.txt).
#
# Full path coverage including tests/. Allowlist is NOT path exclusion —
# findings are filtered after verification by exact Raw or @rule lines.
#
# Usage:
#   tools/trufflehog_with_allowlist.sh
#   tools/trufflehog_with_allowlist.sh --print-candidates
#   tools/trufflehog_with_allowlist.sh --print-all-verified
#
# Env:
#   TRUFFLEHOG_IMAGE / TRUFFLEHOG_VERSION / TRUFFLEHOG_EXTRA_ARGS / ALLOWLIST_PATH
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

IMAGE="${TRUFFLEHOG_IMAGE:-ghcr.io/trufflesecurity/trufflehog}"
VERSION="${TRUFFLEHOG_VERSION:-latest}"
ALLOWLIST_PATH="${ALLOWLIST_PATH:-security/trufflehog-allowlist.txt}"
MODE="scan"
case "${1:-}" in
  --print-candidates) MODE="candidates" ;;
  --print-all-verified) MODE="all" ;;
  "") MODE="scan" ;;
  -h|--help)
    sed -n '2,16p' "$0"
    exit 0
    ;;
  *)
    echo "unknown arg: $1" >&2
    exit 2
    ;;
esac

if [[ ! -f "$ALLOWLIST_PATH" ]]; then
  echo "error: allowlist missing: $ALLOWLIST_PATH" >&2
  exit 2
fi

TMP_JSON="$(mktemp)"
trap 'rm -f "$TMP_JSON"' EXIT

# shellcheck disable=SC2086
docker run --rm -v "$ROOT:/tmp" -w /tmp \
  "${IMAGE}:${VERSION}" \
  git file:///tmp/ \
  --only-verified \
  --json \
  --no-update \
  --no-fail \
  ${TRUFFLEHOG_EXTRA_ARGS:-} \
  >"$TMP_JSON" 2>/dev/null || true

python3 - "$ALLOWLIST_PATH" "$MODE" "$TMP_JSON" <<'PY'
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

allowlist_path = Path(sys.argv[1])
mode = sys.argv[2]
json_path = Path(sys.argv[3])


@dataclass(frozen=True)
class Rule:
    detector: str  # exact DetectorName, case-sensitive as emitted by trufflehog
    raw_re: re.Pattern[str]


def load_allowlist(path: Path) -> tuple[set[str], list[Rule]]:
    exact: set[str] = set()
    rules: list[Rule] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("@rule"):
            # @rule detector=Lob raw_regex=^test_[a-z0-9_]+$
            parts = s.split()
            if parts[0] != "@rule":
                raise SystemExit(f"{path}:{lineno}: bad @rule line")
            kv: dict[str, str] = {}
            for p in parts[1:]:
                if "=" not in p:
                    raise SystemExit(f"{path}:{lineno}: expected key=value in @rule")
                k, v = p.split("=", 1)
                kv[k] = v
            det = kv.get("detector")
            rx = kv.get("raw_regex")
            if not det or not rx:
                raise SystemExit(
                    f"{path}:{lineno}: @rule requires detector= and raw_regex="
                )
            try:
                rules.append(Rule(detector=det, raw_re=re.compile(rx)))
            except re.error as exc:
                raise SystemExit(f"{path}:{lineno}: bad raw_regex: {exc}") from exc
            continue
        exact.add(s)
    return exact, rules


def raw_of(f: dict) -> str:
    return (f.get("Raw") or "").strip()


def is_allowed(f: dict, exact: set[str], rules: list[Rule]) -> bool:
    raw = raw_of(f)
    if not raw:
        return False
    if raw in exact:
        return True
    det = f.get("DetectorName") or ""
    for rule in rules:
        if det == rule.detector and rule.raw_re.fullmatch(raw):
            return True
    return False


exact, rules = load_allowlist(allowlist_path)

findings: list[dict] = []
for line in json_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        findings.append(json.loads(line))
    except json.JSONDecodeError:
        continue

verified = [f for f in findings if f.get("Verified")]

if mode == "all":
    for r in sorted({raw_of(f) for f in verified if raw_of(f)}):
        print(r)
    raise SystemExit(0)

if mode == "candidates":
    for r in sorted(
        {
            raw_of(f)
            for f in verified
            if raw_of(f) and not is_allowed(f, exact, rules)
        }
    ):
        print(r)
    raise SystemExit(0)

blocked = [f for f in verified if raw_of(f) and not is_allowed(f, exact, rules)]
ignored = [f for f in verified if raw_of(f) and is_allowed(f, exact, rules)]

print(
    f"trufflehog: verified={len(verified)} "
    f"allowlisted={len(ignored)} remaining={len(blocked)} "
    f"(allowlist={allowlist_path} exact={len(exact)} rules={len(rules)})"
)

if not blocked:
    print("trufflehog: clean (no non-allowlisted verified secrets)")
    raise SystemExit(0)

print("trufflehog: NON-ALLOWLISTED verified findings:", file=sys.stderr)
for f in blocked:
    meta = ((f.get("SourceMetadata") or {}).get("Data") or {}).get("Git") or {}
    path = meta.get("file") or "?"
    line_no = meta.get("line") or "?"
    det = f.get("DetectorName") or "?"
    raw = raw_of(f)
    print(f"  [{det}] {path}:{line_no} raw={raw!r}", file=sys.stderr)
print(
    "Add a confirmed false positive to security/trufflehog-allowlist.txt "
    "(@rule or exact Raw), or rotate real credentials.",
    file=sys.stderr,
)
raise SystemExit(183)
PY
