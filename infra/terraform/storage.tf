# Volumen de datos persistente e independiente de la instancia: Kafka,
# TimescaleDB, respaldos locales y certificados de Caddy (/data).
# Sobrevive a reinicios y al reemplazo de la instancia (se vuelve a adjuntar).

resource "aws_ebs_volume" "data" {
  availability_zone = local.availability_zone
  size              = var.data_volume_size_gb
  type              = "gp3"
  iops              = var.data_volume_iops
  encrypted         = true
  kms_key_id        = var.data_volume_kms_key_arn
  final_snapshot    = var.data_volume_final_snapshot

  tags = {
    Name          = "${local.name}-data"
    FleetData     = "true"
    SnapshotGroup = local.name
  }
}

resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.app.id
  # Al reemplazar la instancia se detiene antes de desacoplar el volumen.
  stop_instance_before_detaching = true
}

# Snapshots periódicos del volumen de datos (Data Lifecycle Manager).
data "aws_iam_policy_document" "assume_dlm" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["dlm.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dlm" {
  count              = var.snapshots_enabled ? 1 : 0
  name               = "${local.name}-dlm"
  assume_role_policy = data.aws_iam_policy_document.assume_dlm.json
}

resource "aws_iam_role_policy_attachment" "dlm" {
  count      = var.snapshots_enabled ? 1 : 0
  role       = aws_iam_role.dlm[0].name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}

resource "aws_dlm_lifecycle_policy" "data" {
  count              = var.snapshots_enabled ? 1 : 0
  description        = "Snapshots de ${local.name}-data"
  execution_role_arn = aws_iam_role.dlm[0].arn
  state              = "ENABLED"

  policy_details {
    resource_types = ["VOLUME"]
    target_tags    = { SnapshotGroup = local.name }

    schedule {
      name      = "cada-${var.snapshot_interval_hours}h"
      copy_tags = true

      create_rule {
        interval      = var.snapshot_interval_hours
        interval_unit = "HOURS"
        times         = ["03:00"]
      }

      retain_rule {
        count = var.snapshot_retain_count
      }

      tags_to_add = { SnapshotCreator = "dlm" }
    }
  }
}

# Bucket opcional para respaldos lógicos de PostgreSQL. No se fuerza su
# borrado: con objetos dentro, terraform destroy falla y los respaldos quedan.
resource "aws_s3_bucket" "backups" {
  count         = var.backup_bucket_enabled ? 1 : 0
  bucket_prefix = "${local.name}-backups-"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "backups" {
  count                   = var.backup_bucket_enabled ? 1 : 0
  bucket                  = aws_s3_bucket.backups[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  count  = var.backup_bucket_enabled ? 1 : 0
  bucket = aws_s3_bucket.backups[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = var.data_volume_kms_key_arn == null ? "AES256" : "aws:kms"
      kms_master_key_id = var.data_volume_kms_key_arn
    }
  }
}

resource "aws_s3_bucket_versioning" "backups" {
  count  = var.backup_bucket_enabled ? 1 : 0
  bucket = aws_s3_bucket.backups[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  count  = var.backup_bucket_enabled ? 1 : 0
  bucket = aws_s3_bucket.backups[0].id
  rule {
    id     = "expirar-respaldos"
    status = "Enabled"
    filter {
      prefix = "postgres/"
    }
    expiration {
      days = var.backup_retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
  }
}

resource "aws_s3_bucket_policy" "backups" {
  count  = var.backup_bucket_enabled ? 1 : 0
  bucket = aws_s3_bucket.backups[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "SoloTLS"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.backups[0].arn, "${aws_s3_bucket.backups[0].arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}
