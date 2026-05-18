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

# ── SUBCOMMAND: lyra-acl ──────────────────────────────────────────────────────
# Exercises the bug from #1089: lyra-acl genkeys renders auth.conf, then
# nats-server -t -c parse-gates it. Three semantic invariants are enforced:
#   (a) every nkey value is exactly 56 characters
#   (b) nk seed→pubkey round-trip matches stored nkey (if nk is available)
#   (c) no literal newline inside any quoted string (embedded-newline guard)
cmd_lyra_acl() {
  local tmpdir="$1"
  require_nats_server

  local seeds_dir="${tmpdir}/nkeys"
  mkdir -p "${seeds_dir}"

  echo "==> lyra-acl: rendering auth.conf into ${seeds_dir}" >&2

  # Run lyra-acl genkeys --regen-authconf.
  # SEEDS_DIR redirects the read path (seeds) and the write path (auth.conf).
  # AUTH_DIR bypass is not needed here — we only use --regen-authconf which
  # reads seeds from SEEDS_DIR and writes auth.conf there (no root required).
  #
  # NOTE: seeds must already exist in seeds_dir for --regen-authconf to work.
  # In CI, the workflow pre-populates seeds via a prior step. For local runs,
  # point SEEDS_DIR at ~/.lyra/nkeys/ (which has real seeds) or seed the dir
  # manually. This helper does not generate seeds — only validates the render.
  if [[ -z "$(ls -A "${seeds_dir}" 2>/dev/null | grep '\.seed$' || true)" ]]; then
    echo "warning: no *.seed files in ${seeds_dir}" >&2
    echo "  For CI: the workflow pre-populates seeds before calling this subcommand." >&2
    echo "  For local: copy ~/.lyra/nkeys/*.seed into ${seeds_dir}/" >&2
    echo "  Falling back to --template-only (fake nkeys) for parse-gate only." >&2
    # --template-only emits fake 56-char nkeys (UDET+uppercase-name pattern);
    # the length and syntax checks will still fire but seed→pubkey round-trip is skipped.
    SEEDS_DIR="${seeds_dir}" uv run --directory "${REPO_ROOT}" \
      lyra-acl genkeys --template-only > "${seeds_dir}/auth.conf"
  else
    SEEDS_DIR="${seeds_dir}" uv run --directory "${REPO_ROOT}" \
      lyra-acl genkeys --regen-authconf
  fi

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
  while IFS= read -r line; do
    # Extract value from: nkey: "UXXXXXXXXX..."
    if [[ "${line}" =~ nkey:[[:space:]]*\"([^\"]+)\" ]]; then
      local nkey="${BASH_REMATCH[1]}"
      local nkey_len="${#nkey}"
      if [[ "${nkey_len}" -ne 56 ]]; then
        echo "error: nkey expected length 56, got ${nkey_len} at: ${line}" >&2
        fail=1
      fi
    fi
  done <<< "${content}"
  [[ "${fail}" -eq 0 ]] || exit 1
  echo "    nkey lengths OK" >&2

  # ── Invariant (b): seed→pubkey round-trip (requires nk binary) ─────────────
  # For each user that has both an nkey line and a matching .seed file in
  # seeds_dir, recompute the public key from the seed and compare.
  if command -v nk &>/dev/null; then
    echo "==> lyra-acl: verifying seed→pubkey round-trip with nk" >&2
    local rt_fail=0
    while IFS= read -r line; do
      # Match comment lines that identify the user: # <name>
      if [[ "${line}" =~ ^[[:space:]]*#[[:space:]]+([a-z0-9_-]+)[[:space:]]*$ ]]; then
        local identity="${BASH_REMATCH[1]}"
        local seed_file="${seeds_dir}/${identity}.seed"
        # Peek at the next nkey line — we parse the full file sequentially,
        # so track the last seen identity comment per nkey line.
        last_identity="${identity}"
      fi
      if [[ "${line}" =~ nkey:[[:space:]]*\"([^\"]+)\" ]]; then
        local stored_nkey="${BASH_REMATCH[1]}"
        local seed_file="${seeds_dir}/${last_identity:-}.seed"
        if [[ -n "${last_identity:-}" && -f "${seed_file}" ]]; then
          local computed_nkey
          computed_nkey="$(nk -inkey "${seed_file}" -pubout 2>/dev/null | tr -d '[:space:]')"
          if [[ "${computed_nkey}" != "${stored_nkey}" ]]; then
            echo "error: seed→pubkey mismatch for '${last_identity}'" >&2
            echo "  stored nkey:   ${stored_nkey}" >&2
            echo "  computed nkey: ${computed_nkey}" >&2
            rt_fail=1
          fi
        fi
        last_identity=""
      fi
    done <<< "${content}"
    [[ "${rt_fail}" -eq 0 ]] || exit 1
    echo "    seed→pubkey round-trip OK" >&2
  else
    echo "warning: nk not on PATH — skipping seed→pubkey round-trip check" >&2
    echo "  In CI, nk is installed by the workflow's NK install step." >&2
  fi

  # ── Invariant (c): no embedded newlines inside quoted strings ──────────────
  # Defends against the secondary failure mode from #1089: if a quoted value
  # spans multiple lines (e.g. nkey: "ABC\nDEF"), nats-server rejects the
  # config because the value contains a literal newline. We check that every
  # line starting with `nkey:` opens and closes its double-quote on the same
  # line (i.e. the full value is inline, not multi-line).
  echo "==> lyra-acl: checking for embedded newlines in quoted strings" >&2
  local nl_fail=0
  while IFS= read -r line; do
    local stripped="${line#"${line%%[![:space:]]*}"}"  # ltrim
    if [[ "${stripped}" == nkey:* ]]; then
      # A well-formed nkey line starts with nkey: " and ends with "
      if ! [[ "${stripped}" =~ ^nkey:[[:space:]]*\"[^\"]+\"[[:space:]]*$ ]]; then
        echo "error: nkey line does not open and close quote on same line: ${line}" >&2
        nl_fail=1
      fi
    fi
  done <<< "${content}"
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
  # Strip the TLS stanza cert_file/key_file/ca_file references since we don't
  # have real certs here; nats-server -t only syntax-checks, doesn't connect.
  # Actually nats-server -t -c DOES validate that referenced files exist for
  # TLS — so we need to either provide dummy certs or strip the tls block.
  # Strategy: copy the conf, replace cert paths with /dev/null (always exists).
  local nats_conf_src="${REPO_ROOT}/deploy/nats/nats.conf"
  local nats_conf_dst="${tmpdir}/nats.conf"

  sed \
    -e 's|cert_file:.*|cert_file: "/dev/null"|' \
    -e 's|key_file:.*|key_file:  "/dev/null"|' \
    -e 's|ca_file:.*|ca_file:   "/dev/null"|' \
    "${nats_conf_src}" > "${nats_conf_dst}"

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
  # gen-certs.sh checks (id -u) == 0 when CERT_DIR=/etc/nats/certs (default).
  # With CERT_DIR overridden to a user-writable tmpdir, the chown commands
  # (root:nats) will still fail for non-root. Use sudo if available in CI;
  # otherwise rely on the runner having root (containers, etc.).
  if [[ "$(id -u)" -eq 0 ]]; then
    CERT_DIR="${tmpdir}" bash "${gen_certs_sh}"
  elif command -v sudo &>/dev/null; then
    sudo env CERT_DIR="${tmpdir}" bash "${gen_certs_sh}"
    # Fix ownership so subsequent steps (openssl, nats-server) can read files.
    sudo chown -R "$(id -u):$(id -g)" "${tmpdir}"
  else
    echo "error: gen-certs.sh requires root or sudo" >&2
    exit 1
  fi

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
