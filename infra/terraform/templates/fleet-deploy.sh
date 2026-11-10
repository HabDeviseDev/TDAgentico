#!/bin/bash
# Obtiene una revisión exacta del repositorio y ejecuta su script de despliegue.
# Uso (como root): fleet-deploy [SHA de 40 caracteres]
# Sin argumento usa FLEET_GIT_REVISION de /etc/fleet/deploy.env.
# Cada revisión queda en /opt/fleet/releases/<SHA> (inmutable); volver atrás
# es desplegar el SHA anterior.
set -euo pipefail

# shellcheck source=/dev/null
source /etc/fleet/deploy.env

rev="${1:-$FLEET_GIT_REVISION}"
if [[ ! "$rev" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR revisión inválida: se requiere un SHA completo de 40 caracteres" >&2
  exit 2
fi

release="/opt/fleet/releases/$rev"
if [ ! -f "$release/.fetched" ]; then
  echo "[fleet-deploy] obteniendo $rev de $FLEET_REPO_URL"
  rm -rf "$release.tmp"
  git init -q "$release.tmp"
  git -C "$release.tmp" remote add origin "$FLEET_REPO_URL"
  if [ -n "${FLEET_GIT_SECRET_ARN:-}" ]; then
    # Repositorio privado: el token viaja en variables de entorno del proceso
    # (no en la línea de comandos ni en archivos).
    token=$(aws secretsmanager get-secret-value --region "$FLEET_REGION" \
      --secret-id "$FLEET_GIT_SECRET_ARN" --query SecretString --output text)
    export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.extraHeader
    GIT_CONFIG_VALUE_0="Authorization: Basic $(printf 'x-access-token:%s' "$token" | base64 -w0)"
    export GIT_CONFIG_VALUE_0
    unset token
  fi
  git -C "$release.tmp" fetch -q --depth 1 origin "$rev"
  git -C "$release.tmp" -c advice.detachedHead=false checkout -q FETCH_HEAD
  unset GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0
  actual=$(git -C "$release.tmp" rev-parse HEAD)
  if [ "$actual" != "$rev" ]; then
    echo "ERROR se obtuvo $actual en lugar de $rev" >&2
    exit 3
  fi
  touch "$release.tmp/.fetched"
  mv "$release.tmp" "$release"
fi

exec "$release/infra/deploy/deploy.sh" "$rev"
