#!/bin/bash
# Verificación final de la entrega. Guarda la salida completa de cada paso en
# docs/verificacion/<fecha>/NN-<paso>.log y un resumen con código de salida y
# duración (resumen.tsv).
#
# Cada paso corre en "bash -o errexit -o pipefail": un comando intermedio que
# falla (también dentro de una tubería) hace fallar el paso, aunque el último
# comando del bloque termine bien. Un paso que falla no detiene los
# siguientes, pero el script termina con código 1 y lista los pasos fallidos.
#
# Uso (desde la raíz, con Docker): scripts/verificacion_final.sh [AAAA-MM-DD] [prefijos de pasos...]
#
# Solo para pruebas (tests/unit/test_verificacion_final.py): VERIF_OUT_ROOT
# cambia la carpeta de salida y VALIDATE_SH el validador de infraestructura.
#
# No borra volúmenes ni datos: no usa "down -v". Detiene el stack con
# "docker compose stop" antes de los pasos que no lo necesitan, para no
# competir por CPU (ver docs/auditoria-ia.md, #110 y #121).
set -uo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash en Windows: no traducir rutas de contenedor
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
root_host=$(pwd -W 2>/dev/null || pwd)

day=${1:-$(date -u +%Y-%m-%d)}
shift || true
only=("$@")  # opcional: ejecutar solo los pasos con estos prefijos (p. ej. 05b 06 07)
out="${VERIF_OUT_ROOT:-docs/verificacion}/$day"
validate_sh=${VALIDATE_SH:-infra/scripts/validate.sh}
failed=()
mkdir -p "$out"
summary="$out/resumen.tsv"
if [ ${#only[@]} -eq 0 ] || [ ! -f "$summary" ]; then
  printf 'paso\tcódigo\tsegundos\tcomando\n' > "$summary"
fi

wanted() {
  local name=$1 p
  [ ${#only[@]} -eq 0 ] && return 0
  for p in "${only[@]}"; do [[ "$name" == "$p"* ]] && return 0; done
  return 1
}

step() {
  local name=$1; shift
  wanted "$name" || return 0
  local log="$out/$name.log" start rc
  start=$(date +%s)
  {
    echo "# $name"
    echo "# inicio: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "# comando: $*"
    echo
  } > "$log"
  bash -o errexit -o pipefail -c "$*" >> "$log" 2>&1
  rc=$?
  [ "$rc" -eq 0 ] || failed+=("$name")
  {
    echo
    echo "# fin: $(date -u +%Y-%m-%dT%H:%M:%SZ)  código: $rc  duración: $(( $(date +%s) - start )) s"
  } >> "$log"
  local cmd="$*"
  cmd=$(printf '%s' "$cmd" | tr '\n' ' ' | tr -s ' ')  # una fila por paso
  printf '%s\t%s\t%s\t%s\n' "$name" "$rc" "$(( $(date +%s) - start ))" "$cmd" >> "$summary"
  echo "$name -> código $rc ($(( $(date +%s) - start )) s)"
}

# Los comandos van entre comillas simples a propósito: los expande "bash -c".
# shellcheck disable=SC2016
step 00-entorno 'date -u; echo "commit: $(git rev-parse HEAD)"; git status --short;
  docker version --format "Docker cliente {{.Client.Version}} / servidor {{.Server.Version}}";
  docker compose version; docker info --format "CPU: {{.NCPU}}  Memoria: {{.MemTotal}} bytes"'

# Reconstruye todas las imágenes (incluida la API con ResilientProducer) y
# espera a que el stack quede sano. Conserva los volúmenes existentes.
step 01-stack-build-up 'docker compose up -d --build --wait'
step 02-api-productor 'docker compose exec -T api python -c "import app.ingest as m; print(\"ResilientProducer presente:\", hasattr(m, \"ResilientProducer\"))";
  docker compose ps --format "{{.Service}}\t{{.State}}\t{{.Status}}"'

# Unitarias + aceptación contra el stack real (AT-4 se omite dentro del
# contenedor porque necesita la CLI de Docker; el proveedor real se omite sin
# AGENT_REAL_PROVIDER_TEST=1 y credencial).
step 03-pytest-unitarias-aceptacion 'docker compose --profile test run --rm --build -T tests -v -rfEs -p no:cacheprovider'

# Escenarios de alertas una vez (incluye la alerta retirada por un evento atrasado).
step 04-demo-alertas 'docker compose --profile demo run --rm -T demo-alerts --once'

# Núcleo de la app móvil con SQLite real contra la API real.
step 05-movil-e2e "docker run --rm --network fleet_default -e API_URL=http://api:8000 -v '$root_host/services/mobile:/app' -v fleet-mobile-modules:/app/node_modules -w /app node:22-bookworm-slim sh -c 'npm ci --no-audit --no-fund && npm run e2e'"

# Sin duplicados en la base para el vehículo del e2e (el más reciente MOVIL-DEMO-*).
step 05b-movil-sin-duplicados "docker compose up -d --wait timescaledb && docker compose exec -T timescaledb psql -U fleet -d fleet -c \"WITH v AS (SELECT vehicle_id FROM position_history WHERE vehicle_id LIKE 'MOVIL-DEMO-%' ORDER BY ingested_at DESC LIMIT 1) SELECT h.vehicle_id, count(*) AS filas_historial, count(DISTINCT h.event_id) AS event_ids_distintos, (SELECT count(*) FROM ingested_events e WHERE e.vehicle_id = h.vehicle_id) AS ingested_events FROM position_history h JOIN v USING (vehicle_id) GROUP BY h.vehicle_id\""

step 06-stack-stop 'docker compose stop'

# --no-cache: tsc y vite build se ejecutan aunque la capa exista de antes.
step 07-portal-tipos-build-vitest 'docker build --no-cache --progress=plain --target test services/portal'
step 08-movil-tipos-pruebas-export "docker run --rm -v '$root_host/services/mobile:/app' -v fleet-mobile-modules:/app/node_modules -w /app node:22-bookworm-slim sh -c 'npm ci --no-audit --no-fund && npm run typecheck && npm test && npx expo export --platform android --output-dir /tmp/dist'"
step 09-workflow-orden "docker run --rm -v '$root_host:/w:ro' -w /w python:3.12-slim sh -c 'pip install -q --disable-pip-version-check pyyaml && python scripts/check_workflow_order.py .github/workflows/*.yml'"
step 10-infra-validate-all "'$validate_sh' all"
# La imagen de pruebas no copia infra/: estas pruebas se omiten en el paso 03.
step 11-pruebas-verificador-puertos "docker run --rm -v '$root_host:/w:ro' -w /w python:3.12-slim sh -c 'pip install -q --disable-pip-version-check pytest && python -m pytest -v -p no:cacheprovider -o addopts= tests/unit/test_check_compose_ports.py'"

echo "Resumen: $summary"
if [ ${#failed[@]} -gt 0 ]; then
  echo "FALLARON ${#failed[@]} paso(s): ${failed[*]}" >&2
  exit 1
fi
echo "Todos los pasos ejecutados terminaron con código 0"
