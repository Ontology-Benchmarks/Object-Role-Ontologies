#!/usr/bin/env bash
# Recreate ontop_service with ONTOP_DB_URL=$1 (empty: compose default) and count BPI 2017 events.
set -u
label() { docker inspect -f "{{index .Config.Labels \"$1\"}}" ontop_service; }
COMPOSE=$(label com.docker.compose.project.config_files | cut -d, -f1)
PROJECT=$(label com.docker.compose.project)
OVR="$(dirname "$0")/overrides/smoke.yml"
if [ -n "$1" ]; then
  printf 'services:\n  ontop:\n    environment:\n      ONTOP_DB_URL: "%s"\n' "$1" > "$OVR"
else
  printf 'services:\n  ontop: {}\n' > "$OVR"
fi
docker compose -p "$PROJECT" -f "$COMPOSE" -f "$OVR" up -d --no-build --no-deps --force-recreate ontop 2>&1 | tail -1
for i in $(seq 1 60); do
  out=$(curl -s -m 30 -H 'Accept: application/sparql-results+json' --data-urlencode \
    'query=PREFIX ocel: <https://stl.mie.utoronto.ca/ontologies/ocel/> SELECT (COUNT(?e) AS ?n) WHERE { ?e a ocel:Event }' \
    http://localhost:8080/sparql)
  if echo "$out" | grep -q '"value"'; then echo "OK: $(echo "$out" | grep -o '"value" *: *"[0-9]*"')"; exit 0; fi
  if [ -n "$out" ] && [ $i -gt 10 ]; then echo "ERROR RESPONSE: $(echo "$out" | head -c 400)"; exit 1; fi
  sleep 1
done
echo "TIMEOUT"; docker logs --tail 20 ontop_service 2>&1 | grep -iE "error|exception|duckdb" | head -5
