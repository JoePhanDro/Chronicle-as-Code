#!/usr/bin/env bash
# Starts the stack (API behind the TLS proxy) and checks what a client sees: the certificate in both modes (own files,
# self-signed), the redirect, the cookie, the client address, and the development override without TLS.
# Used in CI and to verify docs/betrieb-tls.md. Needs Docker with Compose 2.24 or newer and openssl.
# Set KEEP=1 to leave the stack running afterwards.
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE=(docker compose)
https_port=${CHRONICLE_HTTPS_PORT:-443}  # other ports only when 80 or 443 are taken on this machine
http_port=${CHRONICLE_HTTP_PORT:-80}

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

own_certs=$(mktemp -d)
cleanup() {
  rm -rf "$own_certs"
  if [ "${KEEP:-0}" != 1 ]; then
    "${COMPOSE[@]}" down -v >/dev/null 2>&1 || true
    docker compose -f docker-compose.yml -f docker-compose.dev.yml down -v >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

export CHRONICLE_ADMIN_USER=admin
export CHRONICLE_ADMIN_PASSWORD=ci-test-passwort-123

# A database from an earlier run would keep its own admin password, so start with an empty volume.
"${COMPOSE[@]}" down -v >/dev/null 2>&1 || true
"${COMPOSE[@]}" --profile import run --rm importer
"${COMPOSE[@]}" up -d --build --wait

# Caddy needs a moment after start; the retries cover that.
curl_retry=(--retry 15 --retry-all-errors --retry-delay 2 -sk -m 10)

health=$(curl "${curl_retry[@]}" -o /dev/null -w '%{http_code}' https://localhost:${https_port}/api/v1/health)
[ "$health" = 200 ] || fail "HTTPS health check answered $health"

# Without files in ./certs the proxy makes its own certificate (Caddy's local CA).
issuer() { openssl s_client -connect localhost:${https_port} -servername localhost </dev/null 2>/dev/null | openssl x509 -noout -issuer; }
served=$(issuer || true)  # a pipe into grep -q would end openssl early and fail under pipefail
case "$served" in *'Caddy Local Authority'*) ;; *) fail "without certificate files the proxy should use its own CA, issuer: $served" ;; esac

redirect=$(curl -s -m 10 -o /dev/null -w '%{http_code}' http://localhost:${http_port}/)
[ "$redirect" = 308 ] || fail "HTTP should redirect to HTTPS (308), answered $redirect"

if curl -s -m 3 -o /dev/null http://localhost:8000/api/v1/health; then
  fail "the API port is still reachable without the proxy"
fi

headers=$(mktemp)
trap 'rm -f "$headers"; cleanup' EXIT
status=$(curl -sk -m 10 -o /dev/null -D "$headers" -w '%{http_code}' -X POST https://localhost:${https_port}/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d "{\"username\":\"$CHRONICLE_ADMIN_USER\",\"password\":\"$CHRONICLE_ADMIN_PASSWORD\"}")
[ "$status" = 200 ] || fail "login with the admin account answered $status"
cookie=$(grep -i '^set-cookie:' "$headers" || true)
grep -qi '; secure' <<<"$cookie" || fail "the session cookie is not marked Secure"
grep -qi 'httponly' <<<"$cookie" || fail "the session cookie is not HttpOnly"

# A forged X-Forwarded-For must not replace the client address in the audit log.
forged=203.0.113.99
status=$(curl -sk -m 10 -o /dev/null -w '%{http_code}' -X POST https://localhost:${https_port}/api/v1/auth/login \
  -H 'content-type: application/json' -H "X-Forwarded-For: $forged" \
  -d '{"username":"admin","password":"falsch-falsch-1"}')
[ "$status" = 401 ] || fail "a wrong password should answer 401, answered $status"

logged_ip=$("${COMPOSE[@]}" exec -T api python -c "
import sqlite3
row = sqlite3.connect('/db/chronicle.ops.db').execute(\"SELECT ip FROM audit_log WHERE action = 'login_failed' ORDER BY pk DESC LIMIT 1\").fetchone()
print(row[0] if row else '')
")
[ -n "$logged_ip" ] || fail "no failed login in the audit log"
[ "$logged_ip" != "$forged" ] || fail "the forged X-Forwarded-For ended up in the audit log"
[ "$logged_ip" != "${CHRONICLE_PROXY_IP:-172.30.0.10}" ] || fail "the audit log shows the proxy instead of the client"

# With cert.pem and key.pem in the certificate folder the proxy serves those.
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=chronicle-test-cert" -addext "subjectAltName=DNS:localhost" \
  -keyout "$own_certs/key.pem" -out "$own_certs/cert.pem" >/dev/null 2>&1
chmod 644 "$own_certs/key.pem"  # the proxy runs as another user than the one who made the file
export CHRONICLE_CERTS_DIR="$own_certs"
"${COMPOSE[@]}" up -d --force-recreate --no-deps --wait proxy
served=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
  served=$(issuer || true)
  case "$served" in *chronicle-test-cert*) break ;; esac
  sleep 2
done
case "$served" in *chronicle-test-cert*) ;; *) fail "the proxy should serve the own certificate files, issuer: $served" ;; esac
unset CHRONICLE_CERTS_DIR

# The development override: the API on 127.0.0.1:8000 over plain HTTP, no proxy.
"${COMPOSE[@]}" down >/dev/null 2>&1
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --wait
dev=$(curl "${curl_retry[@]}" -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/v1/health)
[ "$dev" = 200 ] || fail "the development override answered $dev on port 8000"
if curl -sk -m 3 -o /dev/null https://localhost:${https_port}/api/v1/health; then
  fail "the development override should not start the proxy"
fi

echo "proxy check ok: HTTPS $health, redirect $redirect, cookie Secure, forged header ignored (client $logged_ip), own CA and own files, dev override"
