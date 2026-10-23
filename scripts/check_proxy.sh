#!/usr/bin/env bash
# Starts the stack behind the TLS proxy and checks what a client sees. Used in CI and to verify docs/betrieb-tls.md.
# Needs Docker with Compose 2.24 or newer. Set KEEP=1 to leave the stack running afterwards.
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.proxy.yml)

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

cleanup() {
  if [ "${KEEP:-0}" != 1 ]; then
    "${COMPOSE[@]}" down -v >/dev/null 2>&1 || true
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

health=$(curl "${curl_retry[@]}" -o /dev/null -w '%{http_code}' https://localhost/api/v1/health)
[ "$health" = 200 ] || fail "HTTPS health check answered $health"

redirect=$(curl -s -m 10 -o /dev/null -w '%{http_code}' http://localhost/)
[ "$redirect" = 308 ] || fail "HTTP should redirect to HTTPS (308), answered $redirect"

if curl -s -m 3 -o /dev/null http://localhost:8000/api/v1/health; then
  fail "the API port is still reachable without the proxy"
fi

headers=$(mktemp)
trap 'rm -f "$headers"; cleanup' EXIT
status=$(curl -sk -m 10 -o /dev/null -D "$headers" -w '%{http_code}' -X POST https://localhost/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d "{\"username\":\"$CHRONICLE_ADMIN_USER\",\"password\":\"$CHRONICLE_ADMIN_PASSWORD\"}")
[ "$status" = 200 ] || fail "login with the admin account answered $status"
cookie=$(grep -i '^set-cookie:' "$headers" || true)
grep -qi '; secure' <<<"$cookie" || fail "the session cookie is not marked Secure"
grep -qi 'httponly' <<<"$cookie" || fail "the session cookie is not HttpOnly"

# A forged X-Forwarded-For must not replace the client address in the audit log.
forged=203.0.113.99
status=$(curl -sk -m 10 -o /dev/null -w '%{http_code}' -X POST https://localhost/api/v1/auth/login \
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
[ "$logged_ip" != 172.30.0.10 ] || fail "the audit log shows the proxy instead of the client"

echo "proxy check ok: HTTPS $health, redirect $redirect, cookie Secure, forged header ignored (client $logged_ip)"
