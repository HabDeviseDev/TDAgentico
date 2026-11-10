#!/bin/bash
# Despliegue reproducible de una revisión exacta en la instancia.
# Lo invoca /opt/fleet/bin/fleet-deploy <SHA> (que obtiene el código).
# Se puede repetir: no reinicializa volúmenes ni datos.
# Si falla, informa el paso y la línea; se reintenta con el mismo comando.
set -Eeuo pipefail

rev="${1:?uso: deploy.sh <SHA>}"
release="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export FLEET_RELEASE="$release"
# shellcheck source=infra/deploy/lib.sh
source "$release/infra/deploy/lib.sh"
# shellcheck source=/dev/null
source "$FLEET_ETC/deploy.env"

[ "$(id -u)" = 0 ] || { echo "ERROR ejecute como root (sudo)" >&2; exit 1; }

install -d -m 0755 /var/log/fleet
install -d -m 0700 "$FLEET_STATE"
log_file="/var/log/fleet/deploy-$(date -u +%Y%m%dT%H%M%SZ)-${rev:0:12}.log"
exec > >(tee -a "$log_file") 2>&1

# Un solo despliegue a la vez.
exec 9>"$FLEET_STATE/deploy.lock"
flock -n 9 || { echo "ERROR ya hay un despliegue en curso" >&2; exit 1; }

step="inicio"
on_error() {
  local code=$? line=$1
  echo "ERROR despliegue de ${rev:0:12} falló en el paso «$step» (línea $line, código $code)."
  echo "      Registro: $log_file"
  echo "      Reintentar: sudo /opt/fleet/bin/fleet-deploy $rev"
  printf 'status=failed\nrevision=%s\nstep=%s\nat=%s\n' "$rev" "$step" "$(date -u -Is)" > "$FLEET_STATE/last_deploy"
  exit "$code"
}
trap 'on_error $LINENO' ERR

begin() { step="$1"; fleet_log "== $step"; }

# ---------------------------------------------------------------------------
begin "1/9 verificar revisión"
actual=$(git -C "$release" rev-parse HEAD)
[ "$actual" = "$rev" ] || { echo "ERROR el código en $release es $actual, no $rev"; false; }
for tool in docker jq aws; do command -v "$tool" >/dev/null; done
docker compose version >/dev/null

# ---------------------------------------------------------------------------
begin "2/9 configuración y secretos"
# Lee un secreto de Secrets Manager. Si el valor es JSON, toma la clave $2.
# El valor nunca se imprime.
secret_value() {
  local raw
  raw=$(aws secretsmanager get-secret-value --region "$FLEET_REGION" \
    --secret-id "$1" --query SecretString --output text)
  if [ "${raw:0:1}" = "{" ]; then
    jq -r --arg k "$2" '.[$k] // empty' <<<"$raw"
  else
    printf '%s' "$raw"
  fi
}

umask 077
tmp_secrets=$(mktemp "$FLEET_ETC/.secrets.XXXXXX")
pg_password=$(secret_value "$FLEET_PG_SECRET_ARN" password)
# Solo caracteres seguros en una URL de conexión (sin escapar).
if [[ ! "$pg_password" =~ ^[A-Za-z0-9_-]{16,128}$ ]]; then
  rm -f "$tmp_secrets"
  echo "ERROR el secreto de PostgreSQL debe tener 16-128 caracteres [A-Za-z0-9_-] (no se muestra su valor)"
  false
fi
printf 'POSTGRES_PASSWORD=%s\n' "$pg_password" > "$tmp_secrets"
if [ -n "${FLEET_LLM_SECRET_ARN:-}" ]; then
  llm_key=$(secret_value "$FLEET_LLM_SECRET_ARN" api_key)
  if [[ ! "$llm_key" =~ ^[A-Za-z0-9_-]+$ ]]; then
    rm -f "$tmp_secrets"
    echo "ERROR el secreto del proveedor del modelo no tiene el formato esperado (no se muestra su valor)"
    false
  fi
  printf 'ANTHROPIC_API_KEY=%s\n' "$llm_key" >> "$tmp_secrets"
  unset llm_key
fi
chmod 0600 "$tmp_secrets"
mv -f "$tmp_secrets" "$FLEET_ETC/secrets.env"
unset pg_password

email_option=""
[ -n "${FLEET_ACME_EMAIL:-}" ] && email_option="email $FLEET_ACME_EMAIL"
tmp_env=$(mktemp "$FLEET_ETC/.compose.XXXXXX")
{
  echo "# Generado por deploy.sh ($rev). No editar: use $FLEET_ETC/tuning.env"
  echo "FLEET_DATA_DIR=$FLEET_DATA_DIR"
  echo "SITE_ADDRESS=$FLEET_SITE_ADDRESS"
  echo "CADDY_EMAIL_OPTION=\"$email_option\""
  # Ajustes opcionales del operador (p. ej. AGENT_MODEL, KAFKA_HEAP_OPTS).
  [ -f "$FLEET_ETC/tuning.env" ] && cat "$FLEET_ETC/tuning.env"
} > "$tmp_env"
chmod 0600 "$tmp_env"
mv -f "$tmp_env" "$FLEET_ETC/compose.env"
umask 022
fleet_compose config --quiet

# ---------------------------------------------------------------------------
begin "3/9 almacenamiento persistente"
if ! mountpoint -q "$FLEET_DATA_DIR"; then
  vol_id="${FLEET_DATA_VOLUME_ID//-/}"
  device=""
  for _ in $(seq 1 60); do  # hasta 5 min: el volumen se adjunta después del arranque
    for candidate in "/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${vol_id}" /dev/xvdf /dev/sdf; do
      if [ -b "$candidate" ]; then device=$(readlink -f "$candidate"); break 2; fi
    done
    sleep 5
  done
  [ -n "$device" ] || { echo "ERROR no aparece el volumen $FLEET_DATA_VOLUME_ID"; false; }
  fs_type=$(blkid -p -o value -s TYPE "$device" || true)
  children=$(lsblk -nro NAME "$device" | wc -l)
  if [ -z "$fs_type" ] && [ "$children" = 1 ]; then
    fleet_log "volumen nuevo y vacío: creando sistema de archivos XFS"
    mkfs.xfs -q -L fleet-data "$device"
  elif [ "$fs_type" != xfs ]; then
    echo "ERROR $device tiene '$fs_type' o particiones; no se formatea. Revise manualmente."
    false
  fi
  uuid=$(blkid -o value -s UUID "$device")
  install -d -m 0755 "$FLEET_DATA_DIR"
  sed -i "\#[[:space:]]${FLEET_DATA_DIR}[[:space:]]#d" /etc/fstab
  echo "UUID=$uuid $FLEET_DATA_DIR xfs defaults,noatime,nofail,x-systemd.device-timeout=60s 0 2" >> /etc/fstab
  systemctl daemon-reload
  mount "$FLEET_DATA_DIR"
fi
mountpoint -q "$FLEET_DATA_DIR"
install -d -m 0755 "$FLEET_DATA_DIR/timescaledb" "$FLEET_DATA_DIR/caddy/data" "$FLEET_DATA_DIR/caddy/config"
install -d -m 0750 -o 1000 -g 1000 "$FLEET_DATA_DIR/kafka"   # appuser de apache/kafka
install -d -m 0700 "$FLEET_DATA_DIR/backups"

# ---------------------------------------------------------------------------
begin "4/9 imágenes"
fleet_compose pull --quiet --ignore-buildable
fleet_compose build --pull

# ---------------------------------------------------------------------------
begin "5/9 datos (Kafka y TimescaleDB)"
fleet_compose up -d kafka timescaledb
wait_healthy() {
  local deadline=$((SECONDS + $2)) state
  while :; do
    state=$(fleet_service_state "$1")
    case "$state" in healthy) return 0 ;; running) [ "$3" = any ] && return 0 ;; esac
    if [ $SECONDS -ge $deadline ]; then
      echo "ERROR $1 no quedó listo (estado: $state)"
      fleet_compose logs --tail 50 "$1" || true
      return 1
    fi
    sleep 5
  done
}
wait_healthy kafka 300 healthy
wait_healthy timescaledb 600 healthy
# La contraseña del rol se alinea con el secreto en cada despliegue (rotación).
# Va por stdin para que no aparezca en la lista de procesos.
set -a
# shellcheck source=/dev/null
source "$FLEET_ETC/secrets.env"
set +a
printf "ALTER ROLE fleet WITH PASSWORD '%s';\n" "$POSTGRES_PASSWORD" |
  fleet_compose exec -T timescaledb psql -q -U fleet -d fleet -v ON_ERROR_STOP=1
unset POSTGRES_PASSWORD ANTHROPIC_API_KEY

# ---------------------------------------------------------------------------
begin "6/9 migraciones y tópicos"
# migrate se conecta por red con contraseña: también valida el secreto.
fleet_compose run --rm --no-deps migrate
fleet_compose run --rm --no-deps kafka-init

# ---------------------------------------------------------------------------
begin "7/9 servicios"
fleet_compose up -d --remove-orphans
for svc in "${FLEET_SERVICES[@]}"; do
  case "$svc" in
    consumer | caddy) wait_healthy "$svc" 120 any ;;
    *) wait_healthy "$svc" 300 healthy ;;
  esac
done

# ---------------------------------------------------------------------------
begin "8/9 disponibilidad"
if [ "$FLEET_SITE_ADDRESS" = ":80" ]; then
  # Cadena completa: Caddy -> portal (nginx) -> API.
  curl -fsS --retry 10 --retry-delay 3 --retry-all-errors -o /dev/null http://127.0.0.1/config.json
  curl -fsS --retry 10 --retry-delay 3 --retry-all-errors -o /dev/null http://127.0.0.1/v1/vehicles
  fleet_log "portal y API responden por HTTP"
else
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/)
  [ "$code" = 308 ] || { echo "ERROR Caddy no redirige a HTTPS (HTTP $code)"; false; }
  # El certificado depende del DNS público: si aún no está, solo se avisa.
  if curl -fsS --retry 5 --retry-delay 6 --retry-all-errors -o /dev/null \
      --resolve "$FLEET_SITE_ADDRESS:443:127.0.0.1" "https://$FLEET_SITE_ADDRESS/config.json"; then
    fleet_log "HTTPS válido en https://$FLEET_SITE_ADDRESS"
  else
    fleet_log "AVISO sin certificado válido todavía: revise el registro DNS A y 'fleet-ops logs caddy'"
  fi
fi

# ---------------------------------------------------------------------------
begin "9/9 ingesta de prueba"
event_id=$(cat /proc/sys/kernel/random/uuid)
fleet_compose exec -T -e SMOKE_EVENT_ID="$event_id" api python - <<'PY'
import datetime, json, os, urllib.request
event = {
    "schema_version": 1,
    "event_id": os.environ["SMOKE_EVENT_ID"],
    "vehicle_id": "SMOKE-DEPLOY",
    "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "lat": 4.6097, "lon": -74.0817, "speed_kmh": 0.0,
}
req = urllib.request.Request("http://localhost:8000/v1/positions", method="POST",
                             data=json.dumps(event).encode(),
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=15) as resp:
    print("API aceptó el evento de prueba:", resp.status)
PY
found=""
for _ in $(seq 1 30); do
  found=$(fleet_compose exec -T timescaledb psql -tA -U fleet -d fleet \
    -c "SELECT 1 FROM ingested_events WHERE event_id = '$event_id'" || true)
  [ "$found" = 1 ] && break
  sleep 2
done
[ "$found" = 1 ] || { echo "ERROR el evento de prueba $event_id no llegó a TimescaleDB en 60 s"; false; }
fleet_log "evento $event_id persistido (API -> Kafka -> consumidor -> TimescaleDB)"

# ---------------------------------------------------------------------------
step="registrar"
previous=$(cat "$FLEET_STATE/deployed_revision" 2>/dev/null || true)
if [ -n "$previous" ] && [ "$previous" != "$rev" ]; then
  echo "$previous" > "$FLEET_STATE/previous_revision"
fi
echo "$rev" > "$FLEET_STATE/deployed_revision"
ln -sfn "$release" /opt/fleet/current.new
mv -Tf /opt/fleet/current.new "$FLEET_CURRENT"

# Herramientas de operación y temporizadores de esta revisión.
for tool in "$release"/infra/deploy/bin/*; do
  ln -sfn "$FLEET_CURRENT/infra/deploy/bin/$(basename "$tool")" "/usr/local/bin/$(basename "$tool")"
done
install -m 0644 "$release"/infra/deploy/systemd/* /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now fleet-health.timer fleet-backup.timer >/dev/null

printf 'status=ok\nrevision=%s\nat=%s\n' "$rev" "$(date -u -Is)" > "$FLEET_STATE/last_deploy"
fleet_log "despliegue de $rev completo (anterior: ${previous:-ninguno})"
