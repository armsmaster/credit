#!/bin/sh
# Idempotent database bootstrap: roles, schemas, grants.
# Runs as a one-shot container on every `docker compose up`, so adding a new
# microservice means adding it to BUSINESS_SERVICES (and DB_PASSWORD_<NAME> to .env).
#
#   keycloak   -> schema keycloak
#   cds_admin  -> schemas admin, audit, tasks   (platform: users, audit log, task queue)
#   cds_<svc>  -> schema <svc>                  (business services)
#
# Business services get INSERT on audit.* (audit records are written in the same
# transaction as the data change) and DML on tasks.* (shared task queue).
set -eu

: "${POSTGRES_HOST:=postgres}"
: "${BUSINESS_SERVICES:=curves}"
export PGPASSWORD="$POSTGRES_SUPERUSER_PASSWORD"
PSQL="psql -X -q -v ON_ERROR_STOP=1 -h $POSTGRES_HOST -U $POSTGRES_SUPERUSER -d $POSTGRES_DB"

until pg_isready -q -h "$POSTGRES_HOST" -U "$POSTGRES_SUPERUSER" -d "$POSTGRES_DB"; do
  echo "db-init: waiting for postgres..."
  sleep 1
done

ensure_role_and_schema() { # role password schema
  $PSQL -v role="$1" -v pw="$2" -v schema="$3" -v db="$POSTGRES_DB" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', :'role')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'role', :'pw') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'db', :'role') \gexec
SELECT format('CREATE SCHEMA IF NOT EXISTS %I AUTHORIZATION %I', :'schema', :'role') \gexec
SELECT format('ALTER ROLE %I SET search_path = %I', :'role', :'schema') \gexec
SQL
}

echo "db-init: roles and schemas"
$PSQL -c "REVOKE ALL ON SCHEMA public FROM PUBLIC" -c "REVOKE ALL ON DATABASE \"$POSTGRES_DB\" FROM PUBLIC"
ensure_role_and_schema keycloak "$DB_PASSWORD_KEYCLOAK" keycloak
ensure_role_and_schema cds_admin "$DB_PASSWORD_ADMIN" admin
$PSQL -c "CREATE SCHEMA IF NOT EXISTS audit AUTHORIZATION cds_admin" \
      -c "CREATE SCHEMA IF NOT EXISTS tasks AUTHORIZATION cds_admin"

for svc in $BUSINESS_SERVICES; do
  var="DB_PASSWORD_$(echo "$svc" | tr '[:lower:]-' '[:upper:]_')"
  pw="$(printenv "$var" || true)"
  if [ -z "$pw" ]; then
    echo "db-init: $var is not set" >&2
    exit 1
  fi
  role="cds_$(echo "$svc" | tr '-' '_')"
  schema="$(echo "$svc" | tr '-' '_')"
  ensure_role_and_schema "$role" "$pw" "$schema"
  $PSQL -v role="$role" <<'SQL'
SELECT format('GRANT USAGE ON SCHEMA audit, tasks TO %I', :'role') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE cds_admin IN SCHEMA audit GRANT INSERT ON TABLES TO %I', :'role') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE cds_admin IN SCHEMA audit GRANT USAGE ON SEQUENCES TO %I', :'role') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE cds_admin IN SCHEMA tasks GRANT SELECT, INSERT, UPDATE ON TABLES TO %I', :'role') \gexec
SELECT format('GRANT INSERT ON ALL TABLES IN SCHEMA audit TO %I', :'role') \gexec
SELECT format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA audit TO %I', :'role') \gexec
SELECT format('GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA tasks TO %I', :'role') \gexec
SQL
  echo "db-init: service '$svc' -> role $role, schema $schema"
done

echo "db-init: done"
