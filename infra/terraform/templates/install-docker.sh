#!/bin/bash
# Instala Docker (paquete de Amazon Linux 2023) y los plugins Compose y Buildx
# en versiones fijas, verificando su checksum SHA-256 publicado.
# Uso: install-docker <compose vX.Y.Z> <buildx vX.Y.Z> <x86_64|arm64>
# Variable opcional: SKIP_DAEMON=1 (no habilita el servicio; para pruebas en
# contenedor).
set -euo pipefail

compose_version="${1:?versión de compose}"
buildx_version="${2:?versión de buildx}"
arch="${3:?arquitectura}"

case "$arch" in
  x86_64) compose_arch=x86_64; buildx_arch=amd64 ;;
  arm64) compose_arch=aarch64; buildx_arch=arm64 ;;
  *) echo "ERROR arquitectura no soportada: $arch" >&2; exit 2 ;;
esac

log() { echo "[install-docker] $*"; }

log "paquetes del sistema"
dnf install -y -q docker git jq tar gzip

plugins=/usr/local/lib/docker/cli-plugins
install -d -m 0755 "$plugins"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Descarga un binario y su lista de checksums; falla si el archivo no
# aparece en la lista o si el hash no coincide.
fetch_verified() {
  local base="$1" file="$2" sums="$3" target="$4"
  curl -fsSL --retry 5 --retry-delay 3 -o "$work/$file" "$base/$file"
  curl -fsSL --retry 5 --retry-delay 3 -o "$work/$sums" "$base/$sums"
  local expected
  expected=$(awk -v f="$file" '{name=$2; sub(/^\*/, "", name); if (name == f) print $1}' "$work/$sums")
  if [ -z "$expected" ]; then
    echo "ERROR $file no figura en $sums" >&2; exit 3
  fi
  echo "$expected  $work/$file" | sha256sum -c --quiet -
  install -m 0755 "$work/$file" "$target"
  log "$file verificado e instalado en $target"
}

fetch_verified "https://github.com/docker/compose/releases/download/$compose_version" \
  "docker-compose-linux-$compose_arch" checksums.txt "$plugins/docker-compose"
fetch_verified "https://github.com/docker/buildx/releases/download/$buildx_version" \
  "buildx-$buildx_version.linux-$buildx_arch" checksums.txt "$plugins/docker-buildx"

# Rotación de registros de contenedores (aplica a contenedores nuevos).
install -d -m 0755 /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "5" },
  "live-restore": true
}
EOF

# Docker no arranca sin /data montado: evita que Kafka o PostgreSQL
# inicialicen datos vacíos en el disco raíz si falta el volumen.
install -d -m 0755 /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/10-fleet-data.conf <<'EOF'
[Unit]
RequiresMountsFor=/data
EOF

if [ "${SKIP_DAEMON:-0}" != "1" ]; then
  systemctl daemon-reload
  systemctl enable --now docker
fi

docker --version
docker compose version
docker buildx version
log "listo"
