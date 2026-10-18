#!/bin/sh
# Checks the seeded demo (docker compose run --rm seed) through the public entry point:
# a member never gets the restricted board financials, the admin does, and the other
# company sees none of Acme's documents.
# Usage: demo.sh <base-url> <demo-password>
set -eu
BASE=$1
PASSWORD=$2
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
QUESTION='{"question": "What was Acme'"'"'s revenue in Q3?"}'

login() { # slug email -> jar path
  jar="$WORK/$2.jar"
  curl -fsS -o /dev/null -c "$jar" -H 'Content-Type: application/json' \
    -d "{\"tenant_slug\":\"$1\",\"email\":\"$2\",\"password\":\"$PASSWORD\"}" \
    "$BASE/api/v1/auth/login"
  echo "$jar"
}

ask() { # jar -> response body
  csrf=$(awk '$6 == "veyl_csrf" { print $7 }' "$1")
  curl -fsS -b "$1" -H "X-CSRF-Token: $csrf" -H 'Content-Type: application/json' \
    -d "$QUESTION" "$BASE/api/v1/query"
}

MIA=$(login acme mia@acme.test)
RESPONSE=$(ask "$MIA")
if echo "$RESPONSE" | grep -q 'Q3 Board Financials'; then
  echo "FAIL: a member was shown the restricted board financials: $RESPONSE"
  exit 1
fi
echo "OK: Mia (member) gets nothing from the board financials"

ALICE=$(login acme alice@acme.test)
RESPONSE=$(ask "$ALICE")
echo "$RESPONSE" | grep -q '"document_title":"Q3 Board Financials"' || {
  echo "FAIL: the admin's answer does not cite the board financials: $RESPONSE"
  exit 1
}
echo "OK: Alice (admin) gets an answer citing the board financials"

GARY=$(login globex gary@globex.test)
DOCS=$(curl -fsS -b "$GARY" "$BASE/api/v1/documents")
for title in 'Employee Handbook' 'Engineering Roadmap' 'Q3 Board Financials' 'IT Help FAQ'; do
  if echo "$DOCS" | grep -q "$title"; then
    echo "FAIL: Globex can see Acme's $title: $DOCS"
    exit 1
  fi
done
RESPONSE=$(ask "$GARY")
if echo "$RESPONSE" | grep -q 'Board Financials'; then
  echo "FAIL: Globex was answered from Acme's financials: $RESPONSE"
  exit 1
fi
echo "OK: Gary (Globex) sees nothing from Acme"
