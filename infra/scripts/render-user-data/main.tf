# Renderiza la plantilla real de user_data con valores ficticios. No declara
# proveedores: no requiere credenciales ni descargar el proveedor de AWS.
# Usa el mismo módulo deploy-config que infra/terraform/main.tf, así que
# prueba las expresiones reales con las opciones ausentes (null) y
# configuradas. Lo usa infra/scripts/validate.sh (terraform console) para
# revisar el resultado con bash -n y shellcheck.

variable "auto_deploy" {
  type    = bool
  default = true
}

variable "optional_configured" {
  description = "true = dominio, correo, secretos opcionales y bucket configurados; false = todos null."
  type        = bool
  default     = false
}

locals {
  templates = "${path.module}/../../terraform/templates"
  optional  = var.optional_configured
  user_data = templatefile("${local.templates}/user_data.sh.tftpl", {
    deploy_env         = module.deploy_config.deploy_env
    compose_version    = "v5.6.1"
    buildx_version     = "v0.38.1"
    architecture       = "x86_64"
    auto_deploy        = var.auto_deploy
    install_docker_b64 = filebase64("${local.templates}/install-docker.sh")
    fleet_deploy_b64   = filebase64("${local.templates}/fleet-deploy.sh")
  })
}

module "deploy_config" {
  source = "../../terraform/modules/deploy-config"

  environment           = "demo"
  region                = "us-east-1"
  repo_url              = "https://github.com/HabDeviseDev/TDAgentico.git"
  git_revision          = "0123456789abcdef0123456789abcdef01234567"
  domain_name           = local.optional ? "flota.example.com" : null
  acme_email            = local.optional ? "ops@example.com" : null
  postgres_secret_arn   = "arn:aws:secretsmanager:us-east-1:111122223333:secret:fleet/demo/postgres-AbCdEf"
  anthropic_secret_arn  = local.optional ? "arn:aws:secretsmanager:us-east-1:111122223333:secret:fleet/demo/anthropic-AbCdEf" : null
  git_token_secret_arn  = local.optional ? "arn:aws:secretsmanager:us-east-1:111122223333:secret:fleet/demo/git-AbCdEf" : null
  data_volume_id        = "vol-0123456789abcdef0"
  backup_bucket         = local.optional ? "fleet-demo-backups-111122223333" : null
  backup_retention_days = 30
  metric_namespace      = "Fleet/fleet-demo"
}

output "user_data_b64" {
  value = base64encode(local.user_data)
}

# EC2 admite hasta 16 KB de user_data (compute.tf lo envía comprimido).
output "user_data_gzip_b64_bytes" {
  value = length(base64gzip(local.user_data))
}
