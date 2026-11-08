#!/bin/bash
# Validación de la infraestructura SIN credenciales de AWS. Todas las
# herramientas corren en contenedores con versión fija (solo requiere Docker).
# La usan el desarrollador y el workflow .github/workflows/infra.yml.
#
# Uso: infra/scripts/validate.sh [terraform|tftest|user_data|tflint|trivy|shell|compose|caddy|install|backup|all]
#
# Cubre la validación SINTÁCTICA y estática. No sustituye: un plan con
# proveedor y cuenta reales, un despliegue real ni las pruebas de recuperación
# (ver infra/README.md, "Niveles de verificación").
set -euo pipefail

export MSYS_NO_PATHCONV=1  # Git Bash en Windows: no traducir rutas de contenedor
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
host_root=$(pwd -W 2>/dev/null || pwd)

TERRAFORM_IMAGE=hashicorp/terraform:1.17.1
TFLINT_IMAGE=ghcr.io/terraform-linters/tflint:v0.65.0
TRIVY_IMAGE=aquasec/trivy:0.76.2
SHELLCHECK_IMAGE=koalaman/shellcheck:v0.11.0
CADDY_IMAGE=caddy:2.11.9
AL2023_IMAGE=amazonlinux:2023
ALPINE_IMAGE=alpine:3.22

run_in() { local image=$1; shift; docker run --rm -v "$host_root:/w" -w "/w/$1" "$image" "${@:2}"; }
# El proveedor de AWS pesa cientos de MB: su directorio de datos va en un
# volumen de Docker y no en la carpeta del repositorio (lento en Windows).
run_tf() {
  docker run --rm -i -v "$host_root:/w" -v fleet-tfdata:/tfdata -e TF_DATA_DIR=/tfdata \
    -w /w/infra/terraform "$TERRAFORM_IMAGE" "$@"
}

SCRIPTS=(
  infra/deploy/deploy.sh infra/deploy/lib.sh
  infra/deploy/bin/fleet-health infra/deploy/bin/fleet-ops
  infra/deploy/bin/fleet-backup infra/deploy/bin/fleet-restore
  infra/terraform/templates/install-docker.sh infra/terraform/templates/fleet-deploy.sh
  infra/scripts/validate.sh
)

check_terraform() {
  echo "== terraform fmt -check / init -backend=false / validate"
  run_tf fmt -check -recursive -diff
  run_in "$TERRAFORM_IMAGE" infra/scripts/render-user-data fmt -check -recursive -diff
  run_tf init -backend=false -input=false -lockfile=readonly >/dev/null
  run_tf validate
  check_tftest
  check_user_data
}

# Terraform sin proveedores en un directorio del repositorio; .terraform va a
# un directorio temporal del contenedor (no ensucia el repositorio).
run_tf_noprov() {
  local dir=$1; shift
  docker run --rm -i -v "$host_root:/w" -w "/w/$dir" -e TF_DATA_DIR=/tmp/tfdata \
    --entrypoint sh "$TERRAFORM_IMAGE" -c "terraform init -backend=false -input=false >/dev/null && $*"
}

# Pruebas de las expresiones reales del módulo deploy-config (opciones
# ausentes, configuradas y mixtas). No requiere el proveedor de AWS.
check_tftest() {
  echo "== terraform test (modules/deploy-config)"
  run_tf_noprov infra/terraform/modules/deploy-config terraform test
}

# Renderiza user_data (sin proveedor de AWS) con las opciones ausentes y
# configuradas, y revisa el script resultante.
check_user_data() {
  echo "== plantilla de user_data: renderizado, tamaño, valores, bash -n, shellcheck"
  local ad opt out rendered size env_block
  for opt in false true; do
    for ad in true false; do
      # Expresión de Terraform (no de shell): las comillas simples son intencionales.
      # shellcheck disable=SC2016
      out=$(echo '"${base64encode(local.user_data)} ${length(base64gzip(local.user_data))}"' |
        run_tf_noprov infra/scripts/render-user-data \
          "terraform console -var auto_deploy=$ad -var optional_configured=$opt" | tr -d '"\r\n')
      rendered=$(printf '%s' "${out% *}" | base64 -d)
      size=${out##* }
      [ "$size" -le 16384 ] || { echo "user_data comprimido ocupa $size bytes (> 16 KB)"; return 1; }
      env_block=$(printf '%s\n' "$rendered" | sed -n '/^cat > \/etc\/fleet\/deploy.env/,/^EOF$/p')
      if [ "$opt" = false ]; then
        for k in FLEET_ACME_EMAIL FLEET_LLM_SECRET_ARN FLEET_GIT_SECRET_ARN FLEET_BACKUP_BUCKET; do
          grep -qx "$k=''" <<<"$env_block" || { echo "$k debería ser '' sin la opción"; return 1; }
        done
        grep -qx "FLEET_SITE_ADDRESS=':80'" <<<"$env_block" || { echo "FLEET_SITE_ADDRESS debería ser :80"; return 1; }
      else
        if ! { grep -qx "FLEET_ACME_EMAIL='ops@example.com'" <<<"$env_block" &&
          grep -qx "FLEET_SITE_ADDRESS='flota.example.com'" <<<"$env_block" &&
          grep -qx "FLEET_BACKUP_BUCKET='fleet-demo-backups-111122223333'" <<<"$env_block" &&
          grep -q "^FLEET_LLM_SECRET_ARN='arn:aws:secretsmanager:.*anthropic" <<<"$env_block" &&
          grep -q "^FLEET_GIT_SECRET_ARN='arn:aws:secretsmanager:.*git" <<<"$env_block"; }; then
          echo "deploy.env no contiene las opciones configuradas"
          printf '%s\n' "$env_block"
          return 1
        fi
      fi
      bash -n <(printf '%s' "$rendered")
      printf '%s' "$rendered" | docker run --rm -i "$SHELLCHECK_IMAGE" -S style -
      echo "opciones=$opt auto_deploy=$ad: $(printf '%s' "$rendered" | wc -l) líneas, $size bytes comprimido, sin hallazgos"
    done
  done
}

check_tflint() {
  echo "== tflint"
  run_in "$TFLINT_IMAGE" infra/terraform --init >/dev/null
  run_in "$TFLINT_IMAGE" infra/terraform --format compact
}

check_trivy() {
  echo "== trivy config (errores de configuración HIGH/CRITICAL)"
  # Con los valores por defecto y con todos los recursos opcionales activados.
  local extra
  for extra in "" "/w/infra/scripts/trivy-all-features.tfvars"; do
    docker run --rm -v "$host_root:/w" "$TRIVY_IMAGE" config --quiet \
      --severity HIGH,CRITICAL --exit-code 1 ${extra:+--tf-vars "$extra"} \
      --ignorefile /w/infra/terraform/.trivyignore.yaml /w/infra/terraform
  done
}

check_shell() {
  echo "== bash -n y shellcheck"
  for f in "${SCRIPTS[@]}"; do bash -n "$f"; done
  docker run --rm -v "$host_root:/w" -w /w "$SHELLCHECK_IMAGE" -x -S style "${SCRIPTS[@]}"
  echo "${#SCRIPTS[@]} scripts sin errores"
}

check_compose() {
  echo "== docker compose config (local + override de AWS) y puertos"
  POSTGRES_PASSWORD=ValidacionFicticia_0123456789 SITE_ADDRESS=:80 \
    docker compose -f docker-compose.yml -f infra/deploy/compose.aws.yml --profile '*' config --quiet
  python3 infra/scripts/check_compose_ports.py 2>/dev/null || python infra/scripts/check_compose_ports.py
}

# fleet-backup y fleet-restore reales con AWS CLI y Docker Compose simulados
# (tests/unit/test_fleet_backup_restore.py). Corre en Linux con bash y jq.
check_backup() {
  echo "== fleet-backup / fleet-restore con AWS CLI y Compose simulados"
  docker run --rm -v "$host_root:/w:ro" -w /w "$ALPINE_IMAGE" sh -c \
    'apk add --no-cache -q bash jq py3-pytest >/dev/null &&
     python3 -m pytest -q tests/unit/test_fleet_backup_restore.py -p no:cacheprovider -o addopts=""'
}

check_caddy() {
  echo "== caddy validate (con y sin dominio)"
  for site in ":80" "flota.example.com"; do
    docker run --rm -v "$host_root/infra/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" \
      -e SITE_ADDRESS="$site" -e CADDY_EMAIL_OPTION="email ops@example.com" \
      "$CADDY_IMAGE" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile > /tmp/caddy-validate.log 2>&1 ||
      { cat /tmp/caddy-validate.log; return 1; }
    echo "SITE_ADDRESS=$site: configuración válida"
  done
}

check_install() {
  echo "== instalación de Docker, Compose y Buildx en Amazon Linux 2023 (contenedor)"
  # nofile acotado: con el límite enorme de Docker, dnf/rpm recorren todos los
  # descriptores al cerrar y tardan decenas de minutos (no ocurre en EC2).
  docker run --rm --ulimit nofile=1024:524288 -e SKIP_DAEMON=1 -v "$host_root/infra/terraform/templates:/t:ro" "$AL2023_IMAGE" \
    bash /t/install-docker.sh v5.6.1 v0.38.1 x86_64
}

target="${1:-all}"
case "$target" in
  all) for t in terraform tflint trivy shell compose backup caddy install; do "check_$t"; done ;;
  terraform | tftest | user_data | tflint | trivy | shell | compose | caddy | install | backup) "check_$target" ;;
  *) echo "objetivo desconocido: $target" >&2; exit 2 ;;
esac
echo "OK ($target)"
