# Perfil de la instancia, con permisos acotados:
# - Systems Manager (Session Manager y Run Command): política administrada mínima.
# - Secrets Manager: GetSecretValue SOLO sobre los secretos indicados.
# - KMS: Decrypt solo si el secreto usa una clave propia y solo vía Secrets Manager.
# - CloudWatch: métricas propias (salud y disco) en su espacio de nombres.
# - S3: escribir y leer respaldos solo en el bucket de respaldos, si existe.

data "aws_iam_policy_document" "assume_ec2" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "${local.name}-instance"
  assume_role_policy = data.aws_iam_policy_document.assume_ec2.json
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "instance" {
  statement {
    sid       = "ReadDeploySecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = local.secret_arns
  }

  dynamic "statement" {
    for_each = var.secrets_kms_key_arn == null ? [] : [var.secrets_kms_key_arn]
    content {
      sid       = "DecryptSecretsViaSecretsManager"
      actions   = ["kms:Decrypt"]
      resources = [statement.value]
      condition {
        test     = "StringEquals"
        variable = "kms:ViaService"
        values   = ["secretsmanager.${var.region}.amazonaws.com"]
      }
    }
  }

  statement {
    sid       = "PutOwnMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"] # PutMetricData no admite recursos; se acota por espacio de nombres
    condition {
      test     = "StringLike"
      variable = "cloudwatch:namespace"
      values   = [local.metric_ns]
    }
  }

  dynamic "statement" {
    for_each = var.backup_bucket_enabled ? [1] : []
    content {
      sid       = "WriteBackups"
      actions   = ["s3:PutObject", "s3:GetObject", "s3:ListBucket"]
      resources = [aws_s3_bucket.backups[0].arn, "${aws_s3_bucket.backups[0].arn}/postgres/*"]
    }
  }
}

resource "aws_iam_role_policy" "instance" {
  name   = "${local.name}-instance"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.instance.json
}

resource "aws_iam_instance_profile" "instance" {
  name = "${local.name}-instance"
  role = aws_iam_role.instance.name
}
