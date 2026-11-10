#!/bin/bash
# Funciones compartidas por los scripts de operación en la instancia.
# Se incluye con: source /opt/fleet/current/infra/deploy/lib.sh
# Las variables se usan en los scripts que incluyen este archivo.
# shellcheck disable=SC2034

# Las rutas admiten sustitución por variables de entorno solo para las
# pruebas (tests/unit/test_fleet_backup_restore.py); en la instancia se usan
# los valores por defecto.
FLEET_ETC=${FLEET_ETC:-/etc/fleet}
FLEET_STATE=${FLEET_STATE:-/var/lib/fleet}
FLEET_DATA_DIR=${FLEET_DATA_DIR:-/data}
FLEET_CURRENT=${FLEET_CURRENT:-/opt/fleet/current}

# Grupo y topic de la ingesta (docker-compose.yml: KAFKA_GROUP_ID y kafka-init).
FLEET_KAFKA_GROUP=fleet-ingestion
FLEET_KAFKA_TOPIC=fleet.positions.v1

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

# Herramienta de Kafka dentro del contenedor: fleet_kafka <script.sh> <args>
fleet_kafka() { fleet_compose exec -T kafka "/opt/kafka/bin/$1" --bootstrap-server localhost:9092 "${@:2}"; }

# Offsets confirmados del grupo de ingesta como arreglo JSON ordenado:
# [{"partition":0,"offset":123}, ...]. Falla si Kafka no responde, si el
# topic no aparece o si alguna partición no tiene offset confirmado ("-").
fleet_kafka_committed_offsets() {
  local out rows
  out=$(fleet_kafka kafka-consumer-groups.sh --describe --group "$FLEET_KAFKA_GROUP" --offsets 2>/dev/null) || return 1
  # Columnas: GROUP TOPIC PARTITION CURRENT-OFFSET LOG-END-OFFSET LAG ...
  rows=$(awk -v t="$FLEET_KAFKA_TOPIC" '$2 == t && $3 ~ /^[0-9]+$/ { print $3, $4 }' <<<"$out")
  [ -n "$rows" ] || return 1
  awk '$2 !~ /^[0-9]+$/ { exit 1 }' <<<"$rows" || return 1
  awk '{ printf "{\"partition\":%s,\"offset\":%s}\n", $1, $2 }' <<<"$rows" | jq -cs 'sort_by(.partition)'
}

# Particiones existentes del topic de ingesta, una por línea.
fleet_kafka_partitions() {
  fleet_kafka kafka-topics.sh --describe --topic "$FLEET_KAFKA_TOPIC" 2>/dev/null |
    sed -nE 's/.*Partition: ([0-9]+).*/\1/p'
}

# Offset más antiguo aún retenido por partición: "<partición> <offset>".
fleet_kafka_earliest_offsets() {
  fleet_kafka kafka-get-offsets.sh --topic "$FLEET_KAFKA_TOPIC" --time -2 2>/dev/null |
    awk -F: '{ print $(NF-1), $NF }'
}

fleet_sha256() { sha256sum "$1" | cut -d' ' -f1; }
