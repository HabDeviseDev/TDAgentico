#!/usr/bin/env bash
# Escenarios de caos controlado sobre un proyecto Compose APARTE (fleet-chaos),
# con volúmenes propios. El stack principal (proyecto "fleet") no se toca.
#
#   loadtest/chaos.sh up                 # levanta el proyecto de caos
#   loadtest/chaos.sh consumer           # A: consumidor detenido durante la ingesta
#   loadtest/chaos.sh db                 # B: TimescaleDB detenida durante la ingesta
#   loadtest/chaos.sh kafka              # C: Kafka detenido; reenvío de ambiguos
#   loadtest/chaos.sh down               # detiene el proyecto (conserva sus volúmenes)
#
# Variables: VEHICLES (30), RATE (10), DURATION (60), FAULT_AT (15 s), FAULT_FOR (25 s),
#            MAX_WAIT (180 s de reconciliación), SEED (1).
# Cada escenario restaura los servicios al salir (trap), aunque falle una comprobación,
# y tiene tiempos máximos en todas las esperas.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export MSYS_NO_PATHCONV=1
export COMPOSE_PROJECT=fleet-chaos
COMPOSE=(docker compose -p fleet-chaos -f docker-compose.yml -f loadtest/compose.chaos.yml)
SERVICES=(kafka kafka-init timescaledb migrate api consumer)
VEHICLES="${VEHICLES:-30}" RATE="${RATE:-10}" DURATION="${DURATION:-60}"
FAULT_AT="${FAULT_AT:-15}" FAULT_FOR="${FAULT_FOR:-25}" MAX_WAIT="${MAX_WAIT:-180}" SEED="${SEED:-1}"
export API_URL="http://api:8000"

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "${LOG:-/dev/null}"; }

restore() {
  log "Restaurando servicios del proyecto de caos (kafka, timescaledb, consumer, api)…"
  timeout 180 "${COMPOSE[@]}" start kafka timescaledb >/dev/null 2>&1
  timeout 180 "${COMPOSE[@]}" start api consumer >/dev/null 2>&1
  "${COMPOSE[@]}" ps --format '{{.Service}} {{.State}}' 2>/dev/null | tee -a "${LOG:-/dev/null}"
}

lag() {  # mensajes en el tópico, confirmados por el consumidor y lag (desde la red del proyecto)
  timeout 60 loadtest/lt.sh lag 2>/dev/null | tail -1
}

up() {
  log "Levantando el proyecto fleet-chaos (puede tardar varios minutos la primera vez)…"
  timeout 900 "${COMPOSE[@]}" up -d --build --wait "${SERVICES[@]}"
}

wait_api() {  # espera acotada a que la API responda dentro de la red
  local deadline=$(( $(date +%s) + ${1:-120} ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if docker run --rm --network fleet-chaos_default curlimages/curl:8.11.1 -sf http://api:8000/health >/dev/null 2>&1; then
      return 0
    fi
    sleep 3
  done
  return 1
}

scenario() {
  local name="$1" fault_stop="$2" fault_start="$3"
  local run="ch-${name}-$(date -u +%d%H%M%S)"   # <= 24 caracteres
  RUN_DIR="$ROOT/loadtest/runs/$run"; mkdir -p "$RUN_DIR"; LOG="$RUN_DIR/chaos.log"
  trap restore EXIT
  log "Escenario $name · ejecución $run · ${VEHICLES} vehículos, ${RATE}/s, ${DURATION}s, falla en t+${FAULT_AT}s durante ${FAULT_FOR}s"
  up || { log "ERROR: no se pudo levantar el proyecto de caos"; return 2; }
  wait_api 120 || { log "ERROR: la API del proyecto de caos no responde"; return 2; }

  # Carga en segundo plano (k6 en la red fleet-chaos_default).
  MAX_AMBIGUOUS="${MAX_AMBIGUOUS:-100000}" MAX_DROPPED="${MAX_DROPPED:-100000}" \
    timeout $(( DURATION + 600 )) loadtest/lt.sh load "$run" "$VEHICLES" "$RATE" "$DURATION" "$SEED" \
    > "$RUN_DIR/k6-console.txt" 2>&1 &
  local k6_pid=$!

  # La falla se inyecta respecto al inicio REAL de la carga (primer resultado de k6),
  # no respecto al lanzamiento: preparar datos y registrar el entorno puede tardar.
  local points="$RUN_DIR/k6-points-ingest.json" deadline=$(( $(date +%s) + 600 ))
  until grep -q '"lt_result"' "$points" 2>/dev/null; do
    if [ "$(date +%s)" -gt "$deadline" ] || ! kill -0 "$k6_pid" 2>/dev/null; then
      log "ERROR: la carga no empezó"; wait "$k6_pid"; return 2
    fi
    sleep 1
  done
  log "La carga empezó; falla en ${FAULT_AT}s"
  sleep "$FAULT_AT"
  log "Antes de la falla: $(lag)"
  log "FALLA: ${COMPOSE[*]} stop $fault_stop"
  local fault_start_ts; fault_start_ts=$(date +%s)
  timeout 120 "${COMPOSE[@]}" stop $fault_stop >/dev/null 2>&1
  while [ $(( $(date +%s) - fault_start_ts )) -lt "$FAULT_FOR" ]; do
    log "Con la falla (t+$(( $(date +%s) - fault_start_ts ))s): $(lag)"
    sleep 2
  done
  log "RECUPERACIÓN tras $(( $(date +%s) - fault_start_ts ))s: ${COMPOSE[*]} start $fault_start"
  timeout 180 "${COMPOSE[@]}" start $fault_start >/dev/null 2>&1
  log "Tras arrancar: $(lag)"

  wait "$k6_pid"; log "k6 terminó (código $?)"
  tail -n 8 "$RUN_DIR/k6-console.txt" | tee -a "$LOG"
  wait_api 120 || log "AVISO: la API no responde tras la recuperación"
  for t in 1 2 3; do log "Recuperación ($t/3): $(lag)"; sleep 5; done &
  local lag_pid=$!

  if [ "$name" = "kafka" ]; then
    log "Reconciliación previa para extraer los eventos ambiguos…"
    loadtest/lt.sh reconcile "$run" 60 --emit-resend > "$RUN_DIR/reconcile-before-resend.txt" 2>&1
    log "Reenvío de ambiguos con el mismo event_id y contenido"
    loadtest/lt.sh resend "$run" 5 > "$RUN_DIR/resend-console.txt" 2>&1
    tee -a "$LOG" < "$RUN_DIR/resend-console.txt" >/dev/null
    if grep -qE "ambiguos=[1-9]" "$RUN_DIR/resend-console.txt"; then
      PENDING="El reenvío siguió recibiendo 503: el productor de la API no se recupera tras reiniciar Kafka \
(OutOfOrderSequenceNumber en el log de api). Intervención: docker compose -p fleet-chaos restart api; \
COMPOSE_PROJECT=fleet-chaos API_URL=http://api:8000 loadtest/lt.sh resend $run 5 resend2; \
COMPOSE_PROJECT=fleet-chaos loadtest/lt.sh reconcile $run 180"
      log "INTERVENCIÓN PENDIENTE: $PENDING"
    fi
  fi
  wait "$lag_pid" 2>/dev/null
  log "Reconciliación final (espera máx. ${MAX_WAIT}s)…"
  loadtest/lt.sh reconcile "$run" "$MAX_WAIT" > "$RUN_DIR/reconcile-console.txt" 2>&1
  local rc=$?
  tail -n 25 "$RUN_DIR/reconcile-console.txt" | tee -a "$LOG"
  log "Reconciliación: código $rc (0 = sin discrepancias)"
  echo "$run"
  if [ -n "${PENDING:-}" ]; then
    log "Escenario incompleto (código 3): $PENDING"
    return 3
  fi
  return $rc
}

case "${1:-}" in
  up) up ;;
  down) "${COMPOSE[@]}" stop ;;
  consumer) scenario consumer consumer consumer ;;
  db) scenario db timescaledb timescaledb ;;
  kafka) scenario kafka kafka kafka ;;
  *) sed -n '2,16p' "$0"; exit 2 ;;
esac
