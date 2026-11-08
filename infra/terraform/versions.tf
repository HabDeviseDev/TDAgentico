terraform {
  # 1.10+: bloqueo nativo del estado en S3 (use_lockfile) para el backend remoto recomendado.
  required_version = ">= 1.10.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Estado local por defecto. Para un entorno compartido, copiar
  # backend.tf.example a backend.tf (bucket S3 con versionado y bloqueo).
}

provider "aws" {
  region = var.region

  default_tags {
    tags = local.tags
  }
}
