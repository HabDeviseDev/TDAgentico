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

  # null = la primera zona (en orden alfabético) que ofrece instance_type.
  # Comparación explícita con null (variables.tf rechaza ""). Si ninguna zona
  # ofrece el tipo, queda null y la precondición de aws_subnet.public falla
  # con un mensaje claro.
  offered_zones     = sort(data.aws_ec2_instance_type_offerings.in_region.locations)
  availability_zone = var.availability_zone != null ? var.availability_zone : (length(local.offered_zones) > 0 ? local.offered_zones[0] : null)

  # Opciones derivadas y /etc/fleet/deploy.env: ver modules/deploy-config.
  https_enabled = module.deploy_config.https_enabled
  secret_arns   = module.deploy_config.secret_arns
  deploy_env    = module.deploy_config.deploy_env
}

# Sin proveedores: sus expresiones se prueban con `terraform test`.
module "deploy_config" {
  source = "./modules/deploy-config"

  environment           = var.environment
  region                = var.region
  repo_url              = var.repo_url
  git_revision          = var.git_revision
  domain_name           = var.domain_name
  acme_email            = var.acme_email
  postgres_secret_arn   = var.postgres_secret_arn
  anthropic_secret_arn  = var.anthropic_secret_arn
  git_token_secret_arn  = var.git_token_secret_arn
  data_volume_id        = aws_ebs_volume.data.id
  backup_bucket         = var.backup_bucket_enabled ? aws_s3_bucket.backups[0].bucket : null
  backup_retention_days = var.backup_retention_days
  metric_namespace      = local.metric_ns
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
