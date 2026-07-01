#!/usr/bin/env bash
# CI gate — Astryx design-system setup health for apps/dashboard.
# Runs `astryx doctor --json` and fails ONLY on checks with status "fail"
# (Node floor, core<->cli version alignment, missing peer deps). Warnings
# (e.g. theme-not-wired) are tolerated.
# Exit contract (tools/AGENTS.md): 0 = clean, 1 = violation found, 2 = script broke.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dash_dir="${repo_root}/apps/dashboard"
cli="${dash_dir}/node_modules/@astryxdesign/cli/bin/astryx.mjs"

# The script drives the Astryx CLI via `node` directly (scripts/qg check_requires
# has no `node` token), so guard it here with an actionable message.
if ! command -v node >/dev/null 2>&1; then
  echo "astryx_doctor: 'node' not found on PATH (Astryx CLI needs Node >=22.13)" >&2
  exit 2
fi
if [[ ! -f "${cli}" ]]; then
  echo "astryx_doctor: CLI not found at ${cli} (run 'bun install')" >&2
  exit 2
fi

# Capture stdout + stderr. Do NOT treat a non-zero doctor exit as breakage:
# doctor exits 1 on a real "fail" status, which we still want to parse and
# report as a violation (exit 1), not misreport as a broken script (exit 2).
err_file="$(mktemp)"
trap 'rm -f "${err_file}"' EXIT
json="$(cd "${dash_dir}" && node "${cli}" --json doctor 2>"${err_file}")" || true

if [[ -z "${json}" ]]; then
  echo "astryx_doctor: doctor produced no JSON output" >&2
  cat "${err_file}" >&2
  exit 2
fi

# Parser exits 2 (unparseable / unexpected shape) or prints the count of failing
# checks on stdout. `|| exit 2` propagates a parser breakage to the gate.
fails="$(printf '%s' "${json}" | node -e '
  let s = "";
  process.stdin.on("data", (d) => (s += d));
  process.stdin.on("end", () => {
    let j;
    try {
      j = JSON.parse(s);
    } catch {
      process.stderr.write("astryx_doctor: unparseable doctor output\n");
      process.exit(2);
    }
    const checks = j.data && j.data.checks;
    if (!Array.isArray(checks)) {
      process.stderr.write("astryx_doctor: unexpected doctor JSON shape (data.checks not an array)\n");
      process.exit(2);
    }
    const failed = checks.filter((c) => c.status === "fail");
    for (const c of failed) process.stderr.write("FAIL: " + c.id + " — " + c.message + "\n");
    process.stdout.write(String(failed.length));
  });
')" || exit 2

if [[ "${fails}" -gt 0 ]]; then
  echo "astryx_doctor: ${fails} failing check(s)" >&2
  exit 1
fi
echo "astryx_doctor: OK (no failing checks)"
