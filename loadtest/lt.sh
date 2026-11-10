#!/usr/bin/env bash
# Pruebas de carga reproducibles (k6 en Docker) y reconciliación.
#
#   API_URL=http://api:8000 loadtest/lt.sh load <run_id> <vehículos> <tasa/s> <duración_s> [semilla]
#   API_URL=http://api:8000 loadtest/lt.sh batch <run_id> <vehículos> <lotes/s> <duración_s> [semilla] [tamaño_lote]
#   API_URL=http://api:8000 loadtest/lt.sh resend <run_id> [tasa/s]
#   loadtest/lt.sh reconcile <run_id> [espera_máx_s] [--emit-resend]
#
# Variables:
#   API_URL           obligatoria (sin valor por defecto). Dentro de la red de Compose: http://api:8000.
#                     Para un entorno externo, su URL completa (y NETWORK=host o la red adecuada).
#   COMPOSE_PROJECT   proyecto de Compose (por defecto "fleet"; caos: "fleet-chaos").
#   NETWORK           red de Docker para k6 y la reconciliación (por defecto <proyecto>_default).
#   K6_IMAGE          imagen de k6 (por defecto grafana/k6:2.3.0).
#   Umbrales de k6:   P95_MS (500), P99_MS (1500), MAX_UNEXPECTED (0), MAX_AMBIGUOUS (0), MAX_DROPPED (0).
#   PRE_VUS, MAX_VUS, REQUEST_TIMEOUT: ajuste del generador.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if pwd -W >/dev/null 2>&1; then HOST_ROOT="$(pwd -W)"; else HOST_ROOT="$ROOT"; fi
export MSYS_NO_PATHCONV=1
PROJECT="${COMPOSE_PROJECT:-fleet}"
NETWORK="${NETWORK:-${PROJECT}_default}"
K6_IMAGE="${K6_IMAGE:-grafana/k6:2.3.0}"
RUNS="$ROOT/loadtest/runs"
TOOLS_IMAGE="${TOOLS_IMAGE:-fleet-tests}"

need_url() {
  if [ -z "${API_URL:-}" ]; then
    echo "API_URL es obligatoria (p. ej. API_URL=http://api:8000 para el stack local de Compose)" >&2
    exit 2
  fi
}

tools() {  # python con psycopg, confluent_kafka y fleet_contracts (imagen de pruebas)
  docker run --rm --network "$NETWORK" \
    -v "$HOST_ROOT/loadtest:/repo/loadtest" -w /repo \
    -e DATABASE_URL="postgresql://fleet:fleet@timescaledb:5432/fleet" \
    -e KAFKA_BOOTSTRAP_SERVERS="kafka:9092" -e KAFKA_GROUP_ID="fleet-ingestion" \
    -e GIT_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo desconocido)" \
    -e PYTHONPATH=/repo/contracts \
    --entrypoint python "$TOOLS_IMAGE" "$@"
}

env_info() {  # entorno de la ejecución: commit, versiones, recursos, parámetros
  local run="$1" phase="$2"; shift 2
  local file="$RUNS/$run/env-$phase.json"
  {
    echo "{"
    echo "  \"phase\": \"$phase\", \"recorded_at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\","
    echo "  \"git_commit\": \"$(git rev-parse --short HEAD 2>/dev/null)\", \"git_dirty\": $( [ -n "$(git status --porcelain 2>/dev/null)" ] && echo true || echo false ),"
    echo "  \"k6\": \"$(docker run --rm "$K6_IMAGE" version 2>/dev/null | head -1)\","
    echo "  \"docker\": \"$(docker version --format '{{.Server.Version}}' 2>/dev/null)\","
    echo "  \"docker_cpus\": $(docker info --format '{{.NCPU}}' 2>/dev/null || echo null),"
    echo "  \"docker_mem_bytes\": $(docker info --format '{{.MemTotal}}' 2>/dev/null || echo null),"
    echo "  \"compose_project\": \"$PROJECT\", \"network\": \"$NETWORK\", \"api_url\": \"${API_URL:-}\","
    echo "  \"args\": \"$*\""
    echo "}"
  } > "$file"
  docker stats --no-stream --format '{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}}' > "$RUNS/$run/stats-$phase.txt" 2>/dev/null || true
}

k6_run() {  # k6_run <run> <script> <points_name> [env...]
  local run="$1" script="$2" name="$3"; shift 3
  local rc=0
  # --user 0: la carpeta de la ejecución la crea el contenedor de herramientas (root).
  docker run --rm --user 0 --network "$NETWORK" \
    -v "$HOST_ROOT/loadtest/runs:/runs" -v "$HOST_ROOT/loadtest/k6:/scripts:ro" \
    -e RUN_ID="$run" -e API_URL="$API_URL" -e SCENARIO_NAME="$name" \
    -e P95_MS -e P99_MS -e MAX_UNEXPECTED -e MAX_AMBIGUOUS -e MAX_DROPPED -e PRE_VUS -e MAX_VUS -e REQUEST_TIMEOUT \
    "$@" "$K6_IMAGE" run --quiet --out "json=/runs/$run/k6-points-$name.json" "/scripts/$script" || rc=$?
  echo "k6 terminó con código $rc (99 = algún umbral no se cumplió)"
  echo "$rc" > "$RUNS/$run/k6-exit-$name.txt"
}

cmd="${1:-}"; shift || true
case "$cmd" in
  prepare)
    run="$1"; shift
    tools loadtest/prepare.py --run-id "$run" "$@" --out /repo/loadtest/runs
    ;;
  load)
    need_url
    run="$1" vehicles="$2" rate="$3" duration="$4" seed="${5:-1}"
    tools loadtest/prepare.py --run-id "$run" --vehicles "$vehicles" --rate "$rate" --duration "$duration" \
      --seed "$seed" --out /repo/loadtest/runs
    env_info "$run" before load "$@"
    start=$(date +%s)
    k6_run "$run" ingest.js ingest -e RATE="$rate" -e DURATION="$duration"
    echo "{\"wall_seconds\": $(( $(date +%s) - start ))}" > "$RUNS/$run/k6-wall-ingest.json"
    env_info "$run" after load "$@"
    ;;
  batch)
    need_url
    run="$1" vehicles="$2" rate="$3" duration="$4" seed="${5:-1}" size="${6:-20}"
    tools loadtest/prepare.py --run-id "$run" --vehicles "$vehicles" --rate "$rate" --duration "$duration" \
      --seed "$seed" --mode batch --batch-size "$size" --out /repo/loadtest/runs
    env_info "$run" before batch "$@"
    k6_run "$run" batch.js batch -e RATE="$rate" -e DURATION="$duration" -e BATCH_SIZE="$size"
    env_info "$run" after batch "$@"
    ;;
  resend)
    need_url
    run="$1" rate="${2:-5}" name="${3:-resend}"
    n=$(tools -c "import json;print(len(json.load(open('/repo/loadtest/runs/$run/dataset-resend.json'))))")
    if [ "$n" = "0" ]; then echo "No hay eventos ambiguos para reenviar"; exit 0; fi
    duration=$(( (n + rate - 1) / rate + 1 ))
    echo "Reenviando $n eventos ambiguos con el mismo event_id y contenido ($rate/s, ${duration}s)"
    MAX_AMBIGUOUS="${MAX_AMBIGUOUS:-$n}" k6_run "$run" ingest.js "$name" -e DATASET=dataset-resend.json \
      -e RATE="$rate" -e DURATION="$duration"
    ;;
  reconcile)
    run="$1"; wait_s="${2:-120}"; shift 2 || shift $#
    tools loadtest/reconcile.py --run-id "$run" --max-wait "$wait_s" --runs-dir /repo/loadtest/runs "$@"
    ;;
  lag)
    tools -c "import sys; sys.path.insert(0, 'loadtest'); from reconcile import topic_stats; s = topic_stats('kafka:9092', 'fleet-ingestion'); print('kafka sin respuesta' if s is None else f\"mensajes={s['end']} confirmados={s['committed']} lag={s['lag']}\")"
    ;;
  probe)
    need_url
    run="$1" count="${2:-10}"
    docker run --rm --network "$NETWORK" -v "$HOST_ROOT/loadtest:/repo/loadtest" -w /repo \
      -e API_URL="$API_URL" -e PORTAL_URL="${PORTAL_URL:-http://portal}" \
      --entrypoint python "$TOOLS_IMAGE" loadtest/portal_probe.py --run-id "$run" --count "$count" \
      --runs-dir /repo/loadtest/runs
    ;;
  *)
    sed -n '2,20p' "$0"
    exit 2
    ;;
esac
