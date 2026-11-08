locals {
  name = "${var.project}-${var.environment}"

  # Etiquetas en todos los recursos (default_tags del proveedor) y en los
  # volúmenes que crea la instancia (volume_tags).
  tags = merge(var.extra_tags, {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
    Repository  = var.repo_url
  })

  # La AMI sigue la arquitectura del tipo de instancia (Graviton = arm64).
  architecture = contains(data.aws_ec2_instance_type.selected.supported_architectures, "arm64") ? "arm64" : "x86_64"

  availability_zone = coalesce(var.availability_zone, sort(data.aws_ec2_instance_type_offerings.in_region.locations)[0])

  https_enabled = var.domain_name != null
  site_address  = local.https_enabled ? var.domain_name : ":80"

  secret_arns = compact([var.postgres_secret_arn, var.anthropic_secret_arn, var.git_token_secret_arn])

  # Configuración NO secreta que la instancia usa para desplegar. Los valores
  # secretos se leen durante el despliegue desde Secrets Manager.
  deploy_env = {
    FLEET_ENVIRONMENT    = var.environment
    FLEET_REGION         = var.region
    FLEET_REPO_URL       = var.repo_url
    FLEET_GIT_REVISION   = var.git_revision
    FLEET_SITE_ADDRESS   = local.site_address
    FLEET_ACME_EMAIL     = coalesce(var.acme_email, "")
    FLEET_PG_SECRET_ARN  = var.postgres_secret_arn
    FLEET_LLM_SECRET_ARN = coalesce(var.anthropic_secret_arn, "")
    FLEET_GIT_SECRET_ARN = coalesce(var.git_token_secret_arn, "")
    FLEET_DATA_VOLUME_ID = aws_ebs_volume.data.id
    FLEET_BACKUP_BUCKET  = var.backup_bucket_enabled ? aws_s3_bucket.backups[0].bucket : ""
    FLEET_BACKUP_DAYS    = tostring(var.backup_retention_days)
    FLEET_METRIC_NS      = local.metric_ns
  }
}

data "aws_partition" "current" {}

data "aws_ec2_instance_type" "selected" {
  instance_type = var.instance_type
}

data "aws_ec2_instance_type_offerings" "in_region" {
  location_type = "availability-zone"
  filter {
    name   = "instance-type"
    values = [var.instance_type]
  }
}

# Amazon Linux 2023 más reciente para la arquitectura de la instancia.
data "aws_ssm_parameter" "al2023_ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-${local.architecture}"
}
