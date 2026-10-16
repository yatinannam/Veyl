#!/bin/sh
# Asks a question through the public entry point and checks that the answer cites the
# ingested travel policy. Run after ingest.sh. Usage: query.sh <base-url> <cookie-jar>
set -eu
BASE=$1
JAR=$2
CSRF=$(awk '$6 == "veyl_csrf" { print $7 }' "$JAR")
RESPONSE=$(curl -fsS -b "$JAR" -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -d '{"question": "What flights may employees book?"}' "$BASE/api/v1/query")
echo "$RESPONSE" | grep -q '"document_title":"Travel policy"' || {
  echo "FAIL: answer does not cite the travel policy: $RESPONSE"
  exit 1
}
echo "OK: answer cites the travel policy"
