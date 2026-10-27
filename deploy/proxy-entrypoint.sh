#!/bin/sh
# Picks the certificate source for the proxy, then runs Caddy. See docs/betrieb-tls.md.
#   CHRONICLE_TLS=files     /certs/cert.pem and /certs/key.pem
#   CHRONICLE_TLS=internal  a certificate from Caddy's own CA (self-signed, the browser warns)
#   CHRONICLE_TLS=acme      a real certificate for a public domain (Let's Encrypt)
# Unset: "files" if both files exist, otherwise "internal".
set -eu

if [ -z "${CHRONICLE_TLS:-}" ]; then
  if [ -f /certs/cert.pem ] && [ -f /certs/key.pem ]; then
    CHRONICLE_TLS=files
  else
    CHRONICLE_TLS=internal
  fi
fi
case "$CHRONICLE_TLS" in
  files|internal|acme) ;;
  *) echo "Chronicle-Proxy: CHRONICLE_TLS=$CHRONICLE_TLS ist ungültig (files, internal oder acme)" >&2; exit 1 ;;
esac
if [ "$CHRONICLE_TLS" = files ] && { [ ! -f /certs/cert.pem ] || [ ! -f /certs/key.pem ]; }; then
  echo "Chronicle-Proxy: CHRONICLE_TLS=files, aber certs/cert.pem oder certs/key.pem fehlt" >&2
  exit 1
fi

export CHRONICLE_TLS
echo "Chronicle-Proxy: Zertifikat aus '$CHRONICLE_TLS' für ${CHRONICLE_HOSTNAME:-localhost}"
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
