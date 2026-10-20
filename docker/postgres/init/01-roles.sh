#!/bin/sh
# Runs once, on first database initialisation, as the Postgres superuser.
# Creates least-privilege roles: veyl_owner (migrations) and veyl_app (api/worker).
set -e

if [ -z "$VEYL_OWNER_PASSWORD" ] || [ -z "$VEYL_APP_PASSWORD" ]; then
  echo "VEYL_OWNER_PASSWORD and VEYL_APP_PASSWORD must be set" >&2
  exit 1
fi

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v owner_pw="$VEYL_OWNER_PASSWORD" \
  -v app_pw="$VEYL_APP_PASSWORD" \
  -v dbname="$POSTGRES_DB" <<'SQL'
CREATE ROLE veyl_owner LOGIN PASSWORD :'owner_pw'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE veyl_app LOGIN PASSWORD :'app_pw'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;

-- Extensions need superuser; create them here so migrations don't.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS citext;

REVOKE ALL ON DATABASE :"dbname" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"dbname" TO veyl_owner;
-- No TEMPORARY for the app role: temp tables in pg_temp can shadow real tables
-- inside SECURITY DEFINER functions.
GRANT CONNECT ON DATABASE :"dbname" TO veyl_app;

-- Nobody but the superuser needs the maintenance databases.
REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
REVOKE CONNECT ON DATABASE template1 FROM PUBLIC;

ALTER SCHEMA public OWNER TO veyl_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO veyl_app;

-- Functions created by migrations are not executable by default; grant explicitly.
-- Must be global (no IN SCHEMA): a per-schema REVOKE cannot remove the built-in
-- PUBLIC EXECUTE default.
ALTER DEFAULT PRIVILEGES FOR ROLE veyl_owner
  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
SQL
