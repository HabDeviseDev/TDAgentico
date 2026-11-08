# Renderiza la plantilla real de user_data con valores ficticios. No declara
# proveedores: no requiere credenciales ni descargar el proveedor de AWS.
# Lo usa infra/scripts/validate.sh (terraform console) para revisar el
# resultado con bash -n y shellcheck.

variable "auto_deploy" {
  type    = bool
  default = true
}

locals {
  templates = "${path.module}/../../terraform/templates"
  user_data = templatefile("${local.templates}/user_data.sh.tftpl", {
    deploy_env = {
      FLEET_ENVIRONMENT    = "demo"
      FLEET_REGION         = "us-east-1"
      FLEET_REPO_URL       = "https://github.com/HabDeviseDev/TDAgentico.git"
      FLEET_GIT_REVISION   = "0123456789abcdef0123456789abcdef01234567"
      FLEET_SITE_ADDRESS   = "flota.example.com"
      FLEET_ACME_EMAIL     = "ops@example.com"
      FLEET_PG_SECRET_ARN  = "arn:aws:secretsmanager:us-east-1:111122223333:secret:fleet/demo/postgres-AbCdEf"
      FLEET_LLM_SECRET_ARN = ""
      FLEET_GIT_SECRET_ARN = ""
      FLEET_DATA_VOLUME_ID = "vol-0123456789abcdef0"
      FLEET_BACKUP_BUCKET  = ""
      FLEET_BACKUP_DAYS    = "30"
      FLEET_METRIC_NS      = "Fleet/fleet-demo"
    }
    compose_version    = "v5.6.1"
    buildx_version     = "v0.38.1"
    architecture       = "x86_64"
    auto_deploy        = var.auto_deploy
    install_docker_b64 = filebase64("${local.templates}/install-docker.sh")
    fleet_deploy_b64   = filebase64("${local.templates}/fleet-deploy.sh")
  })
}

output "user_data_b64" {
  value = base64encode(local.user_data)
}

# EC2 admite hasta 16 KB de user_data (compute.tf lo envía comprimido).
output "user_data_gzip_b64_bytes" {
  value = length(base64gzip(local.user_data))
}
