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

if [[ ! -f "${cli}" ]]; then
  echo "astryx_doctor: CLI not found at ${cli} (run 'bun install')" >&2
  exit 2
fi

json="$(cd "${dash_dir}" && node "${cli}" --json doctor 2>/dev/null)" || {
  echo "astryx_doctor: doctor command failed to run" >&2
  exit 2
}

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
    const failed = (j.data?.checks ?? []).filter((c) => c.status === "fail");
    for (const c of failed) process.stderr.write("FAIL: " + c.id + " — " + c.message + "\n");
    process.stdout.write(String(failed.length));
  });
')" || exit 2

if [[ "${fails}" -gt 0 ]]; then
  echo "astryx_doctor: ${fails} failing check(s)" >&2
  exit 1
fi
echo "astryx_doctor: OK (no failing checks)"
