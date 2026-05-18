#!/usr/bin/env bash
# check_renderer_roundtrip.sh — renderer→consumer roundtrip helper
#
# Exercises each config renderer through its real downstream consumer in
# check/dry-run mode. Intended for CI (renderer-roundtrip.yml) and local runs.
#
# Usage:
#   tools/check_renderer_roundtrip.sh lyra-acl  <tmpdir>
#   tools/check_renderer_roundtrip.sh nats-conf <tmpdir>
#   tools/check_renderer_roundtrip.sh gen-certs <tmpdir>
#
# <tmpdir>: writable scratch directory; caller is responsible for cleanup.
# nats-server must be on PATH (installed by the workflow's F1 install step).

set -euo pipefail

# ── REPO ROOT ─────────────────────────────────────────────────────────────────
# Resolve repo root relative to this script so all paths are absolute.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── USAGE ─────────────────────────────────────────────────────────────────────
usage() {
  cat >&2 <<'EOF'
Usage: tools/check_renderer_roundtrip.sh <subcommand> <tmpdir>

Subcommands:
  lyra-acl   Render auth.conf via lyra-acl genkeys and parse with nats-server
  nats-conf  Parse deploy/nats/nats.conf + nats-container.conf with nats-server
  gen-certs  Run gen-certs.sh, verify cert chain with openssl, parse TLS stanza
  self-test  Falsifiable self-test: constructs known-bad inputs and asserts each
             invariant fires. nk must be on PATH. No nats-server required.

Each subcommand writes scratch files to <tmpdir>; caller manages cleanup.
nats-server must be on PATH (not required for self-test).
EOF
  exit 1
}

# ── GUARD: require nats-server on PATH ────────────────────────────────────────
require_nats_server() {
  if ! command -v nats-server &>/dev/null; then
    echo "error: nats-server not found on PATH" >&2
    echo "  Install via the F1 step in renderer-roundtrip.yml or ci.yml" >&2
    exit 1
  fi
}

# ── GUARD: require nk on PATH ─────────────────────────────────────────────────
require_nk() {
  if ! command -v nk &>/dev/null; then
    echo "error: nk not found on PATH" >&2
    echo "  In CI: installed by the 'Install nk' step in renderer-roundtrip.yml (lyra-acl-parse job)." >&2
    echo "  Locally: install nkeys v0.4.15 from https://github.com/nats-io/nkeys/releases" >&2
    exit 1
  fi
}

# ── INVARIANT HELPERS ────────────────────────────────────────────────────────
# Extracted from cmd_lyra_acl so cmd_self_test can call them directly with
# crafted inputs. Each function reads auth.conf content from a variable and
# seeds from a directory; exits non-zero if the invariant is violated.

# check_nkey_lengths <content> — Invariant (a): all nkey values exactly 56 chars
check_nkey_lengths() {
  local content="$1"
  local fail=0
  local nkey_count=0
  while IFS= read -r line; do
    if [[ "${line}" =~ nkey:[[:space:]]*\"([^\"]+)\" ]]; then
      nkey_count=$((nkey_count + 1))
      local nkey="${BASH_REMATCH[1]}"
      local nkey_len="${#nkey}"
      if [[ "${nkey_len}" -ne 56 ]]; then
        echo "error: nkey expected length 56, got ${nkey_len} at: ${line}" >&2
        fail=1
      fi
    fi
  done <<< "${content}"
  if [[ "${nkey_count}" -eq 0 ]]; then
    echo "error: no nkey lines found in auth.conf — format may have drifted (expected at least 1)" >&2
    return 1
  fi
  [[ "${fail}" -eq 0 ]] || return 1
  echo "    nkey lengths OK" >&2
}

# check_roundtrip <content> <seeds_dir> — Invariant (b): seed→pubkey round-trip
# auth.conf structure (per-user block):
#   nkey: "U..."        ← nkey comes FIRST
#   # identity-name    ← identity comment comes AFTER the nkey line
# Strategy: save the pending nkey when we see the nkey line; resolve it
# against the seed when we see the identity comment on the next line.
check_roundtrip() {
  local content="$1"
  local seeds_dir="$2"
  local rt_fail=0
  local rt_checked=0
  local pending_nkey=""
  while IFS= read -r line; do
    if [[ "${line}" =~ nkey:[[:space:]]*\"([^\"]+)\" ]]; then
      rt_checked=$((rt_checked + 1))
      pending_nkey="${BASH_REMATCH[1]}"
    elif [[ -n "${pending_nkey}" && "${line}" =~ ^[[:space:]]*#[[:space:]]+([a-z0-9_-]+)[[:space:]]*$ ]]; then
      local identity="${BASH_REMATCH[1]}"
      local seed_file="${seeds_dir}/${identity}.seed"
      if [[ -f "${seed_file}" ]]; then
        local computed_nkey
        computed_nkey="$(nk -inkey "${seed_file}" -pubout | tr -d '[:space:]')"
        if [[ "${computed_nkey}" != "${pending_nkey}" ]]; then
          echo "error: seed→pubkey mismatch for '${identity}'" >&2
          echo "  stored nkey:   ${pending_nkey}" >&2
          echo "  computed nkey: ${computed_nkey}" >&2
          rt_fail=1
        fi
      fi
      pending_nkey=""
    else
      pending_nkey=""
    fi
  done <<< "${content}"
  if [[ "${rt_checked}" -eq 0 ]]; then
    echo "error: no nkey lines found in auth.conf — format may have drifted (expected at least 1)" >&2
    return 1
  fi
  [[ "${rt_fail}" -eq 0 ]] || return 1
  echo "    seed→pubkey round-trip OK" >&2
}

# check_no_embedded_newlines <content> — Invariant (c): no embedded newlines
# Defends against the secondary failure mode from #1089: if a quoted value
# spans multiple lines (e.g. nkey: "ABC\nDEF"), nats-server rejects the
# config because the value contains a literal newline.
check_no_embedded_newlines() {
  local content="$1"
  local nl_fail=0
  local nl_checked=0
  while IFS= read -r line; do
    local stripped="${line#"${line%%[![:space:]]*}"}"  # ltrim
    if [[ "${stripped}" == nkey:* ]]; then
      nl_checked=$((nl_checked + 1))
      if ! [[ "${stripped}" =~ ^nkey:[[:space:]]*\"[^\"]+\"[[:space:]]*$ ]]; then
        echo "error: nkey line does not open and close quote on same line: ${line}" >&2
        nl_fail=1
      fi
    fi
  done <<< "${content}"
  if [[ "${nl_checked}" -eq 0 ]]; then
    echo "error: no nkey lines found in auth.conf — format may have drifted (expected at least 1)" >&2
    return 1
  fi
  [[ "${nl_fail}" -eq 0 ]] || return 1
  echo "    no embedded newlines in quoted strings OK" >&2
}

# ── SUBCOMMAND: lyra-acl ──────────────────────────────────────────────────────
# Exercises the bug from #1089: lyra-acl genkeys renders auth.conf, then
# nats-server -t -c parse-gates it. Three semantic invariants are enforced:
#   (a) every nkey value is exactly 56 characters
#   (b) nk seed→pubkey round-trip matches stored nkey
#   (c) no literal newline inside any quoted string (embedded-newline guard)
cmd_lyra_acl() {
  local tmpdir="$1"
  require_nats_server
  require_nk

  local seeds_dir="${tmpdir}/nkeys"
  mkdir -p "${seeds_dir}"

  local acl_matrix="${REPO_ROOT}/deploy/nats/acl-matrix.json"
  if [[ ! -f "${acl_matrix}" ]]; then
    echo "error: acl-matrix.json not found at ${acl_matrix}" >&2
    exit 1
  fi

  echo "==> lyra-acl: generating seeds from acl-matrix.json into ${seeds_dir}" >&2

  # Read identity names from the acl-matrix.json map and generate a real seed
  # for each one using nk. The seed (private key) is written to <identity>.seed.
  local identities
  identities=$(jq -r '.identities | keys[]' "${acl_matrix}")
  while IFS= read -r identity; do
    # Validate identity name against the canonical charset before using it as a
    # filename component — defends against a malicious acl-matrix.json with
    # `../` traversal or shell-meta keys. Same charset as the auth.conf
    # comment parser (check_roundtrip line 104).
    if ! [[ "${identity}" =~ ^[a-z0-9_-]+$ ]]; then
      echo "error: invalid identity name '${identity}' in acl-matrix.json — must match ^[a-z0-9_-]+\$" >&2
      exit 1
    fi
    nk -gen user > "${seeds_dir}/${identity}.seed"
  done <<< "${identities}"

  echo "==> lyra-acl: rendering auth.conf into ${seeds_dir}" >&2

  # Run lyra-acl genkeys --regen-authconf.
  # SEEDS_DIR redirects the read path (seeds) and the write path (auth.conf).
  # Seeds now always exist — no fallback to --template-only.
  SEEDS_DIR="${seeds_dir}" uv run --directory "${REPO_ROOT}" \
    lyra-acl genkeys --regen-authconf

  local auth_conf="${seeds_dir}/auth.conf"
  if [[ ! -f "${auth_conf}" ]]; then
    echo "error: lyra-acl genkeys did not produce ${auth_conf}" >&2
    exit 1
  fi

  echo "==> lyra-acl: parse-gating with nats-server -t -c" >&2
  nats-server -t -c "${auth_conf}"

  # Read the file once for all invariant checks.
  local content
  content="$(cat "${auth_conf}")"

  echo "==> lyra-acl: checking nkey lengths (expected 56)" >&2
  check_nkey_lengths "${content}" || exit 1

  echo "==> lyra-acl: verifying seed→pubkey round-trip with nk" >&2
  check_roundtrip "${content}" "${seeds_dir}" || exit 1

  echo "==> lyra-acl: checking for embedded newlines in quoted strings" >&2
  check_no_embedded_newlines "${content}" || exit 1

  echo "==> lyra-acl: all checks passed" >&2
}

# ── SUBCOMMAND: nats-conf ─────────────────────────────────────────────────────
# Static parse-gate for deploy/nats/nats.conf and deploy/nats/nats-container.conf.
# Both configs include "nkeys/auth.conf" relative to the config file location;
# we create a minimal stub so nats-server -t -c can resolve the include without
# touching real system files.
cmd_nats_conf() {
  local tmpdir="$1"
  require_nats_server

  # Create stub nkeys/auth.conf — must be parse-valid so the include resolves.
  # Content is the minimum authorization block that nats-server accepts.
  local nkeys_dir="${tmpdir}/nkeys"
  mkdir -p "${nkeys_dir}"
  printf 'authorization {\n  users = []\n}\n' > "${nkeys_dir}/auth.conf"

  # ── nats.conf ─────────────────────────────────────────────────────────────
  # Copy to tmpdir so the include path "nkeys/auth.conf" resolves relative to
  # $tmpdir (i.e. $tmpdir/nkeys/auth.conf — which we just created above).
  # Strip the entire `tls { … }` block: `nats-server -t -c` validates that
  # referenced cert/key/ca files exist and are valid PEM. We don't have real
  # certs here (the gen-certs subcommand covers TLS roundtrip separately).
  # awk depth-counter handles nested braces correctly.
  local nats_conf_src="${REPO_ROOT}/deploy/nats/nats.conf"
  local nats_conf_dst="${tmpdir}/nats.conf"

  awk '
    /^[[:space:]]*tls[[:space:]]*\{/ { depth=1; next }
    depth > 0 {
      for (i=1; i<=length($0); i++) {
        c = substr($0, i, 1)
        if (c == "{") depth++
        else if (c == "}") depth--
      }
      if (depth == 0) next
      next
    }
    { print }
  ' "${nats_conf_src}" > "${nats_conf_dst}"

  # Fix the include path: the original uses `include "nkeys/auth.conf"` which
  # resolves relative to the config file dir. Since our copy is in $tmpdir and
  # our stub is at $tmpdir/nkeys/auth.conf, the relative path is already correct.

  echo "==> nats-conf: parse-gating nats.conf with nats-server -t -c" >&2
  nats-server -t -c "${nats_conf_dst}"
  echo "    nats.conf OK" >&2

  # ── nats-container.conf ───────────────────────────────────────────────────
  # Container config has no TLS block — simpler case.
  local container_conf_src="${REPO_ROOT}/deploy/nats/nats-container.conf"
  local container_conf_dst="${tmpdir}/nats-container.conf"
  cp "${container_conf_src}" "${container_conf_dst}"

  echo "==> nats-conf: parse-gating nats-container.conf with nats-server -t -c" >&2
  nats-server -t -c "${container_conf_dst}"
  echo "    nats-container.conf OK" >&2

  echo "==> nats-conf: all checks passed" >&2
}

# ── SUBCOMMAND: gen-certs ─────────────────────────────────────────────────────
# Cert renderer + dual consumer:
#   1. CERT_DIR=$tmpdir bash deploy/nats/gen-certs.sh  (renders CA + server certs)
#   2. openssl verify -CAfile $tmpdir/ca.crt $tmpdir/server.crt  (chain validation)
#   3. nats-server -t -c <tls.conf>  (parse-gate a minimal TLS stanza)
cmd_gen_certs() {
  local tmpdir="$1"
  require_nats_server

  local gen_certs_sh="${REPO_ROOT}/deploy/nats/gen-certs.sh"

  echo "==> gen-certs: generating certs into ${tmpdir}" >&2
  # Pass --unprivileged so gen-certs.sh skips root check + chown calls.
  CERT_DIR="${tmpdir}" bash "${gen_certs_sh}" --unprivileged

  local ca_crt="${tmpdir}/ca.crt"
  local server_crt="${tmpdir}/server.crt"
  local server_key="${tmpdir}/server.key"

  for f in "${ca_crt}" "${server_crt}" "${server_key}"; do
    if [[ ! -f "${f}" ]]; then
      echo "error: gen-certs.sh did not produce expected file: ${f}" >&2
      exit 1
    fi
  done

  echo "==> gen-certs: verifying cert chain with openssl" >&2
  openssl verify -CAfile "${ca_crt}" "${server_crt}"
  echo "    openssl verify OK" >&2

  # ── Full nats.conf TLS stanza parse-gate ──────────────────────────────────
  # Parse the real nats.conf UNSTRIPPED (with TLS block intact) so that TLS
  # directive typos (e.g. misspelled cipher_suites) are caught in CI.
  # We substitute the prod cert paths with the tmpdir certs and create a minimal
  # stub nkeys/auth.conf so the include resolves.
  local nats_conf_src="${REPO_ROOT}/deploy/nats/nats.conf"
  local nats_full_dst="${tmpdir}/nats-full.conf"
  local nkeys_dir="${tmpdir}/nkeys"
  mkdir -p "${nkeys_dir}"
  printf 'authorization {\n  users = []\n}\n' > "${nkeys_dir}/auth.conf"

  sed \
    -e "s|/etc/nats/certs/server.crt|${server_crt}|g" \
    -e "s|/etc/nats/certs/server.key|${server_key}|g" \
    -e "s|/etc/nats/certs/ca.crt|${ca_crt}|g" \
    "${nats_conf_src}" > "${nats_full_dst}"

  echo "==> gen-certs: parse-gating full nats.conf (TLS stanza unstripped) with nats-server -t -c" >&2
  nats-server -t -c "${nats_full_dst}"
  echo "    nats.conf full TLS parse-gate OK" >&2

  # Emit a minimal TLS-only nats config that references the generated certs.
  # nats-server -t -c verifies that the referenced files exist and are parseable.
  local tls_conf="${tmpdir}/tls.conf"
  cat > "${tls_conf}" <<EOF
# Minimal TLS stanza for nats-server parse-gate (gen-certs roundtrip check).
tls {
  cert_file: "${server_crt}"
  key_file:  "${server_key}"
  ca_file:   "${ca_crt}"
  timeout:   5
}
EOF

  echo "==> gen-certs: parse-gating TLS stanza with nats-server -t -c" >&2
  nats-server -t -c "${tls_conf}"
  echo "    nats-server TLS parse-gate OK" >&2

  echo "==> gen-certs: all checks passed" >&2
}

# ── SUBCOMMAND: self-test ─────────────────────────────────────────────────────
# Falsifiable self-test: synthetically constructs known-bad inputs and calls
# the invariant helper functions directly to assert each one fires as expected.
#
# Cases:
#   (a) 62-char nkey → check_nkey_lengths fires
#   (b) nkey with unclosed quote (embedded newline) → check_no_embedded_newlines fires
#   (c) swapped nkey (different valid seed) → check_roundtrip fires
#   (d) no nkey: lines at all → zero-match guard fires in all 3 checks
#
# Requires: nk on PATH (for case c to generate a second seed).
# Does NOT require nats-server.
cmd_self_test() {
  local tmpdir="$1"
  require_nk

  local overall_fail=0

  # _assert_check_fails: assert that calling a check_* function with given args
  # exits non-zero AND writes expected_pattern to stderr.
  _assert_check_fails() {
    local case_name="$1"
    local expected_pattern="$2"
    shift 2
    local stderr_file
    stderr_file="$(mktemp)"
    local rc=0
    "$@" 2>"${stderr_file}" || rc=$?
    if [[ "${rc}" -eq 0 ]]; then
      echo "FAIL ${case_name}: expected non-zero exit, got 0" >&2
      overall_fail=1
    elif grep -qF "${expected_pattern}" "${stderr_file}"; then
      echo "PASS ${case_name}" >&2
    else
      echo "FAIL ${case_name}: exit=${rc} but stderr did not contain '${expected_pattern}'" >&2
      echo "  actual stderr:" >&2
      sed 's/^/    /' "${stderr_file}" >&2
      overall_fail=1
    fi
    rm -f "${stderr_file}"
  }

  # ── Prepare two seeds for case (c) ───────────────────────────────────────
  # seed1 is the "correct" identity seed; seed2 is the "wrong" seed whose
  # pubkey will be stored in auth.conf to trigger the round-trip mismatch.
  local seed1_dir="${tmpdir}/seed1"
  local seed2_dir="${tmpdir}/seed2"
  mkdir -p "${seed1_dir}" "${seed2_dir}"
  nk -gen user > "${seed1_dir}/testidentity.seed"
  nk -gen user > "${seed2_dir}/testidentity.seed"
  local pubkey2
  pubkey2="$(nk -inkey "${seed2_dir}/testidentity.seed" -pubout | tr -d '[:space:]')"

  # ── Case (a): 62-char nkey → check_nkey_lengths fires ───────────────────
  local bad_62char="UAAAABBBBCCCCDDDDEEEEFFFFGGGGHHHHIIIIJJJJKKKKLLLLMMMMNNNN12"
  local content_a
  content_a="$(printf '      nkey: "%s"\n      # testidentity\n' "${bad_62char}")"
  _assert_check_fails "case-a (62-char nkey)" "expected length 56" \
    check_nkey_lengths "${content_a}"

  # ── Case (b): unclosed quote → check_no_embedded_newlines fires ──────────
  # printf writes a literal newline inside the nkey: value so the line does
  # not close its double-quote — the invariant must detect this.
  local content_b
  content_b="$(printf '      nkey: "UAAA\n"\n      # testidentity\n')"
  _assert_check_fails "case-b (embedded newline)" "does not open and close quote on same line" \
    check_no_embedded_newlines "${content_b}"

  # ── Case (c): swapped pubkey → check_roundtrip fires ─────────────────────
  # auth.conf stores pubkey2, but seeds_dir has seed1's seed for testidentity.
  local content_c
  content_c="$(printf '      nkey: "%s"\n      # testidentity\n' "${pubkey2}")"
  _assert_check_fails "case-c (swapped pubkey)" "seed→pubkey mismatch" \
    check_roundtrip "${content_c}" "${seed1_dir}"

  # ── Case (d): no nkey: lines → zero-match guard fires in all 3 checks ────
  local content_d
  content_d="$(printf 'authorization {\n  users [\n    { username: "testidentity" }\n  ]\n}\n')"
  local dummy_seeds="${tmpdir}/dummy-seeds"
  mkdir -p "${dummy_seeds}"
  _assert_check_fails "case-d-length (no nkey lines)" "no nkey lines found" \
    check_nkey_lengths "${content_d}"
  _assert_check_fails "case-d-roundtrip (no nkey lines)" "no nkey lines found" \
    check_roundtrip "${content_d}" "${dummy_seeds}"
  _assert_check_fails "case-d-newlines (no nkey lines)" "no nkey lines found" \
    check_no_embedded_newlines "${content_d}"

  # ── Summary ──────────────────────────────────────────────────────────────
  if [[ "${overall_fail}" -eq 0 ]]; then
    echo "==> self-test: all cases passed" >&2
  else
    echo "==> self-test: FAILED — one or more cases did not behave as expected" >&2
    exit 1
  fi
}

# ── DISPATCH ──────────────────────────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
  usage
fi

SUBCOMMAND="$1"
shift

case "${SUBCOMMAND}" in
  lyra-acl)
    [[ $# -ge 1 ]] || { echo "error: lyra-acl requires <tmpdir>" >&2; usage; }
    cmd_lyra_acl "$1"
    ;;
  nats-conf)
    [[ $# -ge 1 ]] || { echo "error: nats-conf requires <tmpdir>" >&2; usage; }
    cmd_nats_conf "$1"
    ;;
  gen-certs)
    [[ $# -ge 1 ]] || { echo "error: gen-certs requires <tmpdir>" >&2; usage; }
    cmd_gen_certs "$1"
    ;;
  self-test)
    [[ $# -ge 1 ]] || { echo "error: self-test requires <tmpdir>" >&2; usage; }
    cmd_self_test "$1"
    ;;
  --help|-h)
    usage
    ;;
  *)
    echo "error: unknown subcommand '${SUBCOMMAND}'" >&2
    usage
    ;;
esac
