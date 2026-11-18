#!/bin/sh
# One-shot: ILM retention policy + index template for system logs.
set -eu
ES="${ES_URL:-http://elasticsearch:9200}"
DAYS="${LOG_RETENTION_DAYS:-7}"

until curl -sf "$ES/_cluster/health?wait_for_status=yellow&timeout=5s" >/dev/null; do
  echo "es-setup: waiting for elasticsearch..."
  sleep 3
done

echo "es-setup: ILM policy cds-logs-retention (delete after ${DAYS}d)"
curl -sf -X PUT "$ES/_ilm/policy/cds-logs-retention" -H 'Content-Type: application/json' -d "{
  \"policy\": {
    \"phases\": {
      \"hot\":    { \"actions\": {} },
      \"delete\": { \"min_age\": \"${DAYS}d\", \"actions\": { \"delete\": {} } }
    }
  }
}"
echo

echo "es-setup: index template cds-logs"
curl -sf -X PUT "$ES/_index_template/cds-logs" -H 'Content-Type: application/json' \
  -d @/setup/index-template.json
echo
echo "es-setup: done"
