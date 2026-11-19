#!/bin/bash
# Idempotent Keycloak configuration for EXISTING installations.
# realm-cds.json is imported only on the very first start, so settings introduced later are
# applied here. Runs as a one-shot container on every `docker compose up`; users, passwords
# and roles are never touched. Add new steps at the end, each safe to repeat.
set -euo pipefail

KCADM=/opt/keycloak/bin/kcadm.sh
CFG=/tmp/kcadm.config
SERVER="${KC_INTERNAL_URL:-http://keycloak:8080/auth}"

if ! "$KCADM" config credentials --config "$CFG" --server "$SERVER" --realm master \
      --user "$KC_BOOTSTRAP_ADMIN_USERNAME" --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null; then
  echo "keycloak-config: cannot log in to the master realm with KC_BOOTSTRAP_ADMIN_* from .env." >&2
  echo "keycloak-config: apply the steps below manually in the Keycloak admin console." >&2
  exit 1
fi

client_uuid() {
  "$KCADM" get clients --config "$CFG" -r cds -q "clientId=$1" --fields id --format csv --noquotes
}

# 1. Own login page: the admin service uses password grants of cds-web;
#    Keycloak's hosted login pages are not used by the UI.
CID="$(client_uuid cds-web)"
if [ -z "$CID" ]; then
  echo "keycloak-config: client cds-web not found in realm cds" >&2
  exit 1
fi
"$KCADM" update "clients/$CID" --config "$CFG" -r cds \
  -s directAccessGrantsEnabled=true -s standardFlowEnabled=false
echo "keycloak-config: cds-web — direct access grants on, hosted login off"

echo "keycloak-config: done"
