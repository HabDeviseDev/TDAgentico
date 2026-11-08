# Variables del despliegue de demostración. Ningún valor secreto entra en
# Terraform: solo los ARN de secretos que ya existen en Secrets Manager.

variable "region" {
  description = "Región de AWS (p. ej. us-east-1)."
  type        = string
  validation {
    condition     = can(regex("^[a-z]{2}(-gov)?-[a-z]+-[0-9]$", var.region))
    error_message = "region debe ser un código de región de AWS, p. ej. us-east-1."
  }
}

variable "project" {
  description = "Nombre corto del proyecto (prefijo de recursos y etiqueta Project)."
  type        = string
  default     = "fleet"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,15}$", var.project))
    error_message = "project: 2-16 caracteres en minúsculas, números o guiones, empezando por letra."
  }
}

variable "environment" {
  description = "Nombre del entorno (p. ej. demo, dev). Forma parte de los nombres de recursos."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,15}$", var.environment))
    error_message = "environment: 2-16 caracteres en minúsculas, números o guiones, empezando por letra."
  }
}

variable "instance_type" {
  description = "Tipo de instancia EC2 (Nitro). La arquitectura de la AMI se elige según el tipo (x86_64 o arm64)."
  type        = string
  default     = "t3.large"
  validation {
    condition     = can(regex("^[a-z][a-z0-9]*[0-9][a-z0-9-]*\\.[a-z0-9]+$", var.instance_type))
    error_message = "instance_type debe tener la forma familia.tamaño, p. ej. t3.large o m7g.large."
  }
}

variable "root_volume_size_gb" {
  description = "Tamaño del disco raíz (sistema, imágenes de Docker y logs), en GiB."
  type        = number
  default     = 30
  validation {
    condition     = var.root_volume_size_gb >= 20 && var.root_volume_size_gb <= 500
    error_message = "root_volume_size_gb debe estar entre 20 y 500."
  }
}

variable "data_volume_size_gb" {
  description = "Tamaño del volumen de datos persistente (Kafka, TimescaleDB, respaldos y certificados), en GiB."
  type        = number
  default     = 50
  validation {
    condition     = var.data_volume_size_gb >= 10 && var.data_volume_size_gb <= 2000
    error_message = "data_volume_size_gb debe estar entre 10 y 2000."
  }
}

variable "data_volume_iops" {
  description = "IOPS del volumen de datos gp3 (3000 = base incluida)."
  type        = number
  default     = 3000
  validation {
    condition     = var.data_volume_iops >= 3000 && var.data_volume_iops <= 16000
    error_message = "data_volume_iops debe estar entre 3000 y 16000."
  }
}

variable "data_volume_kms_key_arn" {
  description = "ARN de una clave KMS propia para cifrar los discos. null = clave administrada por AWS (aws/ebs)."
  type        = string
  default     = null
  validation {
    condition     = var.data_volume_kms_key_arn == null || can(regex("^arn:aws[a-z-]*:kms:", var.data_volume_kms_key_arn))
    error_message = "data_volume_kms_key_arn debe ser un ARN de KMS o null."
  }
}

variable "data_volume_final_snapshot" {
  description = "Crear un snapshot final del volumen de datos al destruirlo (conserva los datos tras terraform destroy)."
  type        = bool
  default     = true
}

variable "vpc_cidr" {
  description = "CIDR de la VPC."
  type        = string
  default     = "10.42.0.0/16"
  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0)) && tonumber(split("/", var.vpc_cidr)[1]) <= 24
    error_message = "vpc_cidr debe ser un CIDR IPv4 válido de /24 o mayor."
  }
}

variable "public_subnet_cidr" {
  description = "CIDR de la subred pública (dentro de vpc_cidr)."
  type        = string
  default     = "10.42.1.0/24"
  validation {
    condition     = can(cidrhost(var.public_subnet_cidr, 0))
    error_message = "public_subnet_cidr debe ser un CIDR IPv4 válido."
  }
}

variable "availability_zone" {
  description = "Zona de disponibilidad. null = la primera zona de la región que ofrece instance_type."
  type        = string
  default     = null

  validation {
    condition     = var.availability_zone == null || can(regex("^[a-z]{2}(-gov)?-[a-z]+-[0-9][a-z]$", var.availability_zone))
    error_message = "availability_zone debe ser null o una zona como us-east-1a (no se admite \"\")."
  }
}

variable "allowed_cidrs" {
  description = "CIDR autorizados a usar el portal (HTTP/HTTPS). Kafka, PostgreSQL, API y agente nunca se exponen."
  type        = list(string)
  validation {
    condition     = length(var.allowed_cidrs) > 0 && alltrue([for c in var.allowed_cidrs : can(cidrhost(c, 0))])
    error_message = "allowed_cidrs debe tener al menos un CIDR IPv4 válido (p. ej. 203.0.113.10/32)."
  }
}

variable "create_eip" {
  description = "Asignar una IP elástica (dirección estable para DNS y para la app móvil)."
  type        = bool
  default     = true
}

variable "domain_name" {
  description = "Nombre DNS público del portal (p. ej. flota.example.com). Si se indica, Caddy obtiene un certificado de Let's Encrypt y sirve HTTPS. null = solo HTTP."
  type        = string
  default     = null
  validation {
    condition     = var.domain_name == null || can(regex("^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]{2,}$", var.domain_name))
    error_message = "domain_name debe ser un nombre DNS en minúsculas o null."
  }
}

variable "acme_email" {
  description = "Correo para Let's Encrypt (avisos de vencimiento). Obligatorio si domain_name está definido."
  type        = string
  default     = null
  validation {
    condition     = var.acme_email == null || can(regex("^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$", var.acme_email))
    error_message = "acme_email debe ser una dirección de correo válida o null."
  }
}

variable "repo_url" {
  description = "URL HTTPS del repositorio Git que se despliega."
  type        = string
  default     = "https://github.com/HabDeviseDev/TDAgentico.git"
  validation {
    condition     = can(regex("^https://[A-Za-z0-9._~:/-]+$", var.repo_url))
    error_message = "repo_url debe ser una URL https://."
  }
}

variable "git_revision" {
  description = "Commit exacto (SHA de 40 caracteres) que se despliega. Nunca una rama: el despliegue es inmutable."
  type        = string
  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.git_revision))
    error_message = "git_revision debe ser un SHA de commit completo (40 caracteres hexadecimales)."
  }
}

variable "git_token_secret_arn" {
  description = "ARN de un secreto con un token de lectura del repositorio (solo si es privado). null = repositorio público."
  type        = string
  default     = null
  validation {
    condition     = var.git_token_secret_arn == null || can(regex("^arn:aws[a-z-]*:secretsmanager:", var.git_token_secret_arn))
    error_message = "git_token_secret_arn debe ser un ARN de Secrets Manager o null."
  }
}

variable "postgres_secret_arn" {
  description = "ARN del secreto (ya existente) con la contraseña de PostgreSQL: texto plano o JSON {\"password\": \"...\"}."
  type        = string
  validation {
    condition     = can(regex("^arn:aws[a-z-]*:secretsmanager:", var.postgres_secret_arn))
    error_message = "postgres_secret_arn debe ser un ARN de Secrets Manager."
  }
}

variable "anthropic_secret_arn" {
  description = "ARN del secreto (ya existente) con la clave del proveedor del modelo: texto plano o JSON {\"api_key\": \"...\"}. null = agente sin configurar."
  type        = string
  default     = null
  validation {
    condition     = var.anthropic_secret_arn == null || can(regex("^arn:aws[a-z-]*:secretsmanager:", var.anthropic_secret_arn))
    error_message = "anthropic_secret_arn debe ser un ARN de Secrets Manager o null."
  }
}

variable "secrets_kms_key_arn" {
  description = "ARN de la clave KMS propia que cifra esos secretos (si no usan aws/secretsmanager). Concede kms:Decrypt solo vía Secrets Manager."
  type        = string
  default     = null
  validation {
    condition     = var.secrets_kms_key_arn == null || can(regex("^arn:aws[a-z-]*:kms:", var.secrets_kms_key_arn))
    error_message = "secrets_kms_key_arn debe ser un ARN de KMS o null."
  }
}

variable "auto_deploy" {
  description = "Ejecutar el despliegue en el primer arranque de la instancia. false = solo prepara la instancia; el despliegue se lanza por SSM."
  type        = bool
  default     = true
}

variable "snapshots_enabled" {
  description = "Crear snapshots periódicos del volumen de datos con Data Lifecycle Manager."
  type        = bool
  default     = true
}

variable "snapshot_interval_hours" {
  description = "Cada cuántas horas se toma un snapshot."
  type        = number
  default     = 24
  validation {
    condition     = contains([1, 2, 3, 4, 6, 8, 12, 24], var.snapshot_interval_hours)
    error_message = "snapshot_interval_hours debe ser 1, 2, 3, 4, 6, 8, 12 o 24."
  }
}

variable "snapshot_retain_count" {
  description = "Cantidad de snapshots periódicos que se conservan."
  type        = number
  default     = 7
  validation {
    condition     = var.snapshot_retain_count >= 1 && var.snapshot_retain_count <= 1000
    error_message = "snapshot_retain_count debe estar entre 1 y 1000."
  }
}

variable "backup_bucket_enabled" {
  description = "Crear un bucket S3 cifrado para copiar los respaldos lógicos de PostgreSQL."
  type        = bool
  default     = false
}

variable "backup_retention_days" {
  description = "Días que se conservan los respaldos en S3 (y localmente en /data/backups)."
  type        = number
  default     = 30
  validation {
    condition     = var.backup_retention_days >= 1 && var.backup_retention_days <= 3650
    error_message = "backup_retention_days debe estar entre 1 y 3650."
  }
}

variable "alarm_email" {
  description = "Correo que recibe las alarmas de CloudWatch (requiere confirmar la suscripción). null = alarmas sin notificación."
  type        = string
  default     = null
}

variable "compose_version" {
  description = "Versión del plugin Docker Compose que se instala (se verifica su checksum)."
  type        = string
  default     = "v5.6.1"
  validation {
    condition     = can(regex("^v[0-9]+\\.[0-9]+\\.[0-9]+$", var.compose_version))
    error_message = "compose_version debe tener la forma vX.Y.Z."
  }
}

variable "buildx_version" {
  description = "Versión del plugin Docker Buildx que se instala (se verifica su checksum)."
  type        = string
  default     = "v0.38.1"
  validation {
    condition     = can(regex("^v[0-9]+\\.[0-9]+\\.[0-9]+$", var.buildx_version))
    error_message = "buildx_version debe tener la forma vX.Y.Z."
  }
}

variable "extra_tags" {
  description = "Etiquetas adicionales para todos los recursos (p. ej. CostCenter, Owner)."
  type        = map(string)
  default     = {}
}
