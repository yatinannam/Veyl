#!/bin/sh
# Uploads a text document through the public entry point and waits until the worker has
# ingested it. Expects a cookie jar from a logged-in admin.
# Usage: ingest.sh <base-url> <cookie-jar>
set -eu
BASE=$1
JAR=$2
CSRF=$(awk '$6 == "veyl_csrf" { print $7 }' "$JAR")
# Relative path: works with both Linux curl and Windows curl under Git Bash.
FILE=.veyl-smoke-upload.txt
trap 'rm -f "$FILE"' EXIT
printf 'Travel policy\n\nEmployees may book economy flights.\n' > "$FILE"
ID=$(curl -fsS -b "$JAR" -H "X-CSRF-Token: $CSRF" \
  -F "file=@$FILE;filename=travel.txt" -F "title=Travel policy" \
  -F "classification=internal" -F "allowed_roles=member" \
  "$BASE/api/v1/documents" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')
[ -n "$ID" ] || { echo "FAIL: upload returned no id"; exit 1; }
i=0
STATUS=unknown
while [ $i -lt 60 ]; do
  STATUS=$(curl -fsS -b "$JAR" "$BASE/api/v1/documents/$ID" | sed -n 's/.*"status":"\([a-z]*\)".*/\1/p')
  [ "$STATUS" = "ready" ] && { echo "OK: document $ID ingested"; exit 0; }
  [ "$STATUS" = "failed" ] && { echo "FAIL: ingestion failed"; exit 1; }
  i=$((i + 1))
  sleep 2
done
echo "FAIL: document not ready after 120s (last status: $STATUS)"
exit 1
