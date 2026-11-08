#!/bin/bash
# Funciones compartidas por los scripts de operación en la instancia.
# Se incluye con: source /opt/fleet/current/infra/deploy/lib.sh
# Las variables se usan en los scripts que incluyen este archivo.
# shellcheck disable=SC2034

FLEET_ETC=/etc/fleet
FLEET_STATE=/var/lib/fleet
FLEET_DATA_DIR=${FLEET_DATA_DIR:-/data}
FLEET_CURRENT=/opt/fleet/current

# Servicios de larga duración que deben estar en ejecución (y sanos si
# tienen healthcheck).
FLEET_SERVICES=(kafka timescaledb api consumer agent portal caddy)

# docker compose con el Compose local + override de AWS y los archivos de
# entorno. FLEET_RELEASE permite operar sobre una revisión distinta a la
# actual (durante el despliegue).
fleet_compose() {
  local dir="${FLEET_RELEASE:-$FLEET_CURRENT}"
  docker compose \
    --project-directory "$dir" \
    -f "$dir/docker-compose.yml" \
    -f "$dir/infra/deploy/compose.aws.yml" \
    --env-file "$FLEET_ETC/compose.env" \
    --env-file "$FLEET_ETC/secrets.env" \
    "$@"
}

# Estado de un servicio: healthy | running | <otro estado> | missing
fleet_service_state() {
  local id state health
  id=$(fleet_compose ps -q "$1" 2>/dev/null | head -n1)
  if [ -z "$id" ]; then echo missing; return; fi
  state=$(docker inspect -f '{{.State.Status}}' "$id" 2>/dev/null || echo missing)
  health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$id" 2>/dev/null || true)
  if [ "$state" = running ] && [ -n "$health" ]; then echo "$health"; else echo "$state"; fi
}

fleet_log() { echo "[$(date -u +%H:%M:%SZ)] $*"; }
