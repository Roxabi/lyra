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

Each subcommand writes scratch files to <tmpdir>; caller manages cleanup.
nats-server must be on PATH.
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

  # ── Invariant (a): every nkey value must be exactly 56 characters ──────────
  # nats-server rejects nkeys that are not valid Ed25519 public keys encoded
  # in base32 (56 chars for a user key starting with 'U').
  echo "==> lyra-acl: checking nkey lengths (expected 56)" >&2
  local fail=0
  local nkey_count=0
  while IFS= read -r line; do
    # Extract value from: nkey: "UXXXXXXXXX..."
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
    exit 1
  fi
  [[ "${fail}" -eq 0 ]] || exit 1
  echo "    nkey lengths OK" >&2

  # ── Invariant (b): seed→pubkey round-trip ──────────────────────────────────
  # auth.conf structure (per-user block):
  #   nkey: "U..."        ← nkey comes FIRST
  #   # identity-name    ← identity comment comes AFTER the nkey line
  # Strategy: save the pending nkey when we see the nkey line; resolve it
  # against the seed when we see the identity comment on the next line.
  # nk is guaranteed on PATH (require_nk was called at the top of cmd_lyra_acl).
  echo "==> lyra-acl: verifying seed→pubkey round-trip with nk" >&2
  local rt_fail=0
  local rt_checked=0
  local pending_nkey=""
  while IFS= read -r line; do
    if [[ "${line}" =~ nkey:[[:space:]]*\"([^\"]+)\" ]]; then
      # Save this nkey; the identity comment on the next line will resolve it.
      rt_checked=$((rt_checked + 1))
      pending_nkey="${BASH_REMATCH[1]}"
    elif [[ -n "${pending_nkey}" && "${line}" =~ ^[[:space:]]*#[[:space:]]+([a-z0-9_-]+)[[:space:]]*$ ]]; then
      # Identity comment immediately following a nkey line.
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
      # Any other line resets the pending nkey (guards against stray matches).
      pending_nkey=""
    fi
  done <<< "${content}"
  if [[ "${rt_checked}" -eq 0 ]]; then
    echo "error: no nkey lines found in auth.conf — format may have drifted (expected at least 1)" >&2
    exit 1
  fi
  [[ "${rt_fail}" -eq 0 ]] || exit 1
  echo "    seed→pubkey round-trip OK" >&2

  # ── Invariant (c): no embedded newlines inside quoted strings ──────────────
  # Defends against the secondary failure mode from #1089: if a quoted value
  # spans multiple lines (e.g. nkey: "ABC\nDEF"), nats-server rejects the
  # config because the value contains a literal newline. We check that every
  # line starting with `nkey:` opens and closes its double-quote on the same
  # line (i.e. the full value is inline, not multi-line).
  echo "==> lyra-acl: checking for embedded newlines in quoted strings" >&2
  local nl_fail=0
  local nl_checked=0
  while IFS= read -r line; do
    local stripped="${line#"${line%%[![:space:]]*}"}"  # ltrim
    if [[ "${stripped}" == nkey:* ]]; then
      nl_checked=$((nl_checked + 1))
      # A well-formed nkey line starts with nkey: " and ends with "
      if ! [[ "${stripped}" =~ ^nkey:[[:space:]]*\"[^\"]+\"[[:space:]]*$ ]]; then
        echo "error: nkey line does not open and close quote on same line: ${line}" >&2
        nl_fail=1
      fi
    fi
  done <<< "${content}"
  if [[ "${nl_checked}" -eq 0 ]]; then
    echo "error: no nkey lines found in auth.conf — format may have drifted (expected at least 1)" >&2
    exit 1
  fi
  [[ "${nl_fail}" -eq 0 ]] || exit 1
  echo "    no embedded newlines in quoted strings OK" >&2

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
  --help|-h)
    usage
    ;;
  *)
    echo "error: unknown subcommand '${SUBCOMMAND}'" >&2
    usage
    ;;
esac
