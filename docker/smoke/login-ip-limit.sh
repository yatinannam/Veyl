#!/bin/sh
# Spoofed X-Forwarded-For values must not evade the per-IP login limit.
# Sends IP_LIMIT + 1 failed logins through the public entry point, each with a different
# account and a different forged client IP. The last one must be rate-limited (429).
# Usage: login-ip-limit.sh [base-url] [attempts]   (attempts = 4 x LOGIN_MAX_FAILURES + 1)
set -eu
BASE=${1:-http://127.0.0.1:3000}
ATTEMPTS=${2:-21}
i=1
code=""
while [ "$i" -le "$ATTEMPTS" ]; do
  code=$(curl -s -o /dev/null -w '%{http_code}' \
    -H 'Content-Type: application/json' \
    -H "X-Forwarded-For: 198.51.100.$i" \
    -d "{\"tenant_slug\":\"no-such-tenant\",\"email\":\"probe$i@example.test\",\"password\":\"wrong-password\"}" \
    "$BASE/api/v1/auth/login")
  i=$((i + 1))
done
if [ "$code" != "429" ]; then
  echo "FAIL: attempt $ATTEMPTS returned $code, expected 429 (per-IP limit evaded via X-Forwarded-For)"
  exit 1
fi
echo "OK: per-IP login limit holds against spoofed X-Forwarded-For"
