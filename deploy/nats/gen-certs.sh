#!/usr/bin/env bash
# Generate self-signed CA + NATS server TLS certificate (TLS 1.3 compatible)
#
# Usage: sudo ./deploy/nats/gen-certs.sh [--san "DNS:host.local,IP:192.168.1.16"]
#
# Default SAN: DNS:localhost,IP:127.0.0.1,IP:192.168.1.16
# Outputs: /etc/nats/certs/{ca.key,ca.crt,server.key,server.crt}
#
# Idempotent — skips if certs already exist. Delete /etc/nats/certs/ to regenerate.

set -euo pipefail

CERT_DIR="/etc/nats/certs"
DEFAULT_SAN="DNS:localhost,IP:127.0.0.1,IP:192.168.1.16"
SAN="${DEFAULT_SAN}"
VALID_DAYS=3650  # 10 years — private LAN CA, no ACME; rotate manually on reprovision

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[+]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[x]${NC} $1"; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --san) SAN="$2"; shift 2 ;;
    *) error "Unknown option: $1" ;;
  esac
done

[ "$(id -u)" -eq 0 ] || error "Must be run as root (sudo ./deploy/nats/gen-certs.sh)"

if [ -f "${CERT_DIR}/server.crt" ] && [ -f "${CERT_DIR}/server.key" ]; then
  warn "Certs already exist at ${CERT_DIR}/ — skipping. Delete to regenerate."
  exit 0
fi

# Ensure nats group exists for cert file ownership
getent group nats >/dev/null 2>&1 || groupadd --system nats

mkdir -p "${CERT_DIR}"
chmod 755 "${CERT_DIR}"
chown root:root "${CERT_DIR}"

info "Generating CA private key (ECDSA P-384)..."
openssl ecparam -name secp384r1 -genkey -noout -out "${CERT_DIR}/ca.key"
chmod 600 "${CERT_DIR}/ca.key"
chown root:root "${CERT_DIR}/ca.key"

info "Creating self-signed CA certificate..."
openssl req -new -x509 \
  -key "${CERT_DIR}/ca.key" \
  -out "${CERT_DIR}/ca.crt" \
  -days "${VALID_DAYS}" \
  -subj "/CN=Lyra NATS CA/O=Roxabi"
chmod 644 "${CERT_DIR}/ca.crt"

info "Generating server private key (ECDSA P-384)..."
openssl ecparam -name secp384r1 -genkey -noout -out "${CERT_DIR}/server.key"
chmod 640 "${CERT_DIR}/server.key"
chown root:nats "${CERT_DIR}/server.key"  # nats user reads this via group

info "Creating server certificate (SAN: ${SAN})..."
EXT_FILE=$(mktemp)
trap 'rm -f "${EXT_FILE}" "${CERT_DIR}/server.csr" "${CERT_DIR}/ca.srl"' EXIT

cat > "${EXT_FILE}" << EOF
[v3_ext]
subjectAltName=${SAN}
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
basicConstraints=CA:FALSE
EOF

openssl req -new \
  -key "${CERT_DIR}/server.key" \
  -out "${CERT_DIR}/server.csr" \
  -subj "/CN=nats-server/O=Roxabi"

openssl x509 -req \
  -in "${CERT_DIR}/server.csr" \
  -CA "${CERT_DIR}/ca.crt" \
  -CAkey "${CERT_DIR}/ca.key" \
  -CAcreateserial \
  -out "${CERT_DIR}/server.crt" \
  -days "${VALID_DAYS}" \
  -extfile "${EXT_FILE}" \
  -extensions v3_ext
chmod 644 "${CERT_DIR}/server.crt"

rm -f "${CERT_DIR}/server.csr" "${CERT_DIR}/ca.srl"

info "TLS certificates written to ${CERT_DIR}/"
info "  ca.crt     — distribute to NATS clients (set ca_file in client config)"
info "  server.crt — server certificate"
info "  server.key — server private key (root:nats 640)"
