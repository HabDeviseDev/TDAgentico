# Configuración NO secreta que la instancia usa para desplegar
# (/etc/fleet/deploy.env). Los valores secretos se leen durante el despliegue
# desde Secrets Manager; aquí solo viajan sus ARN.
#
# Módulo sin proveedores ni recursos: sus expresiones se prueban con
# `terraform test` (tests/) sin credenciales ni el proveedor de AWS.
#
# Las opciones ausentes llegan como null y se convierten EXPLÍCITAMENTE en "".
# No se usa coalesce(valor, ""): coalesce descarta tanto null como "" y falla
# ("no non-null, non-empty-string arguments") justo cuando la opción falta.

terraform {
  required_version = ">= 1.7.0"
}

variable "environment" {
  description = "Entorno (demo, staging, prod)."
  type        = string
}

variable "region" {
  description = "Región de AWS."
  type        = string
}

variable "repo_url" {
  description = "URL HTTPS del repositorio."
  type        = string
}

variable "git_revision" {
  description = "SHA de 40 caracteres a desplegar."
  type        = string
}

variable "domain_name" {
  description = "Dominio del portal; null = HTTP en el puerto 80."
  type        = string
  default     = null
}

variable "acme_email" {
  description = "Correo para Let's Encrypt; null = sin correo."
  type        = string
  default     = null
}

variable "postgres_secret_arn" {
  description = "ARN del secreto con la contraseña de PostgreSQL (obligatorio)."
  type        = string

  validation {
    condition     = var.postgres_secret_arn != null && var.postgres_secret_arn != ""
    error_message = "postgres_secret_arn es obligatorio."
  }
}

variable "anthropic_secret_arn" {
  description = "ARN del secreto con la clave del modelo; null = agente sin proveedor real."
  type        = string
  default     = null
}

variable "git_token_secret_arn" {
  description = "ARN del secreto con el token de Git; null = repositorio público."
  type        = string
  default     = null
}

variable "data_volume_id" {
  description = "ID del volumen EBS de datos."
  type        = string
}

variable "backup_bucket" {
  description = "Bucket de respaldos; null = solo respaldos locales."
  type        = string
  default     = null
}

variable "backup_retention_days" {
  description = "Días de retención de los respaldos locales."
  type        = number
}

variable "metric_namespace" {
  description = "Espacio de nombres de las métricas de CloudWatch."
  type        = string
}

locals {
  https_enabled = var.domain_name != null
}

output "https_enabled" {
  description = "true si hay dominio (Caddy sirve HTTPS con certificado automático)."
  value       = local.https_enabled
}

output "site_address" {
  description = "Dirección del sitio para Caddy: el dominio o \":80\"."
  value       = local.https_enabled ? var.domain_name : ":80"
}

output "secret_arns" {
  description = "ARN de los secretos configurados (para la política de IAM)."
  value = [
    for arn in [var.postgres_secret_arn, var.anthropic_secret_arn, var.git_token_secret_arn] :
    arn if arn != null
  ]
}

output "deploy_env" {
  description = "Contenido de /etc/fleet/deploy.env (sin valores secretos)."
  value = {
    FLEET_ENVIRONMENT    = var.environment
    FLEET_REGION         = var.region
    FLEET_REPO_URL       = var.repo_url
    FLEET_GIT_REVISION   = var.git_revision
    FLEET_SITE_ADDRESS   = local.https_enabled ? var.domain_name : ":80"
    FLEET_ACME_EMAIL     = var.acme_email == null ? "" : var.acme_email
    FLEET_PG_SECRET_ARN  = var.postgres_secret_arn
    FLEET_LLM_SECRET_ARN = var.anthropic_secret_arn == null ? "" : var.anthropic_secret_arn
    FLEET_GIT_SECRET_ARN = var.git_token_secret_arn == null ? "" : var.git_token_secret_arn
    FLEET_DATA_VOLUME_ID = var.data_volume_id
    FLEET_BACKUP_BUCKET  = var.backup_bucket == null ? "" : var.backup_bucket
    FLEET_BACKUP_DAYS    = tostring(var.backup_retention_days)
    FLEET_METRIC_NS      = var.metric_namespace
  }
}
