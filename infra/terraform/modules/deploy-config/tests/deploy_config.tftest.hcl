# Pruebas de las expresiones reales del módulo (sin proveedores ni
# credenciales). Ejecutar: infra/scripts/validate.sh tftest

variables {
  environment           = "demo"
  region                = "us-east-1"
  repo_url              = "https://github.com/HabDeviseDev/TDAgentico.git"
  git_revision          = "0123456789abcdef0123456789abcdef01234567"
  postgres_secret_arn   = "arn:aws:secretsmanager:us-east-1:111122223333:secret:fleet/demo/postgres-AbCdEf"
  data_volume_id        = "vol-0123456789abcdef0"
  backup_retention_days = 30
  metric_namespace      = "Fleet/fleet-demo"
}

# Todas las opciones ausentes (null): cadenas vacías explícitas, sin error.
run "opciones_ausentes" {
  command = plan

  assert {
    condition     = output.deploy_env.FLEET_ACME_EMAIL == ""
    error_message = "acme_email null debe producir \"\"."
  }
  assert {
    condition     = output.deploy_env.FLEET_LLM_SECRET_ARN == ""
    error_message = "anthropic_secret_arn null debe producir \"\"."
  }
  assert {
    condition     = output.deploy_env.FLEET_GIT_SECRET_ARN == ""
    error_message = "git_token_secret_arn null debe producir \"\"."
  }
  assert {
    condition     = output.deploy_env.FLEET_BACKUP_BUCKET == ""
    error_message = "backup_bucket null debe producir \"\"."
  }
  assert {
    condition     = output.site_address == ":80" && !output.https_enabled
    error_message = "Sin dominio: HTTP en :80."
  }
  assert {
    condition     = output.secret_arns == [var.postgres_secret_arn]
    error_message = "Solo el secreto de PostgreSQL debe entrar en la política."
  }
  assert {
    condition     = alltrue([for v in values(output.deploy_env) : v != null])
    error_message = "Ningún valor de deploy.env puede ser null."
  }
  assert {
    condition     = output.deploy_env.FLEET_BACKUP_DAYS == "30"
    error_message = "backup_retention_days se convierte a texto."
  }
}

# Todas las opciones configuradas: se copian tal cual.
run "opciones_configuradas" {
  command = plan

  variables {
    domain_name          = "flota.example.com"
    acme_email           = "ops@example.com"
    anthropic_secret_arn = "arn:aws:secretsmanager:us-east-1:111122223333:secret:fleet/demo/anthropic-AbCdEf"
    git_token_secret_arn = "arn:aws:secretsmanager:us-east-1:111122223333:secret:fleet/demo/git-AbCdEf"
    backup_bucket        = "fleet-demo-backups-111122223333"
  }

  assert {
    condition     = output.deploy_env.FLEET_ACME_EMAIL == "ops@example.com"
    error_message = "acme_email configurado debe copiarse."
  }
  assert {
    condition     = output.deploy_env.FLEET_LLM_SECRET_ARN == var.anthropic_secret_arn
    error_message = "anthropic_secret_arn configurado debe copiarse."
  }
  assert {
    condition     = output.deploy_env.FLEET_GIT_SECRET_ARN == var.git_token_secret_arn
    error_message = "git_token_secret_arn configurado debe copiarse."
  }
  assert {
    condition     = output.deploy_env.FLEET_BACKUP_BUCKET == "fleet-demo-backups-111122223333"
    error_message = "backup_bucket configurado debe copiarse."
  }
  assert {
    condition     = output.site_address == "flota.example.com" && output.https_enabled
    error_message = "Con dominio: HTTPS con ese dominio."
  }
  assert {
    condition     = length(output.secret_arns) == 3
    error_message = "Los tres secretos deben entrar en la política."
  }
}

# Mezcla: solo algunas opciones presentes.
run "opciones_mixtas" {
  command = plan

  variables {
    anthropic_secret_arn = "arn:aws:secretsmanager:us-east-1:111122223333:secret:fleet/demo/anthropic-AbCdEf"
  }

  assert {
    condition     = output.deploy_env.FLEET_LLM_SECRET_ARN == var.anthropic_secret_arn && output.deploy_env.FLEET_GIT_SECRET_ARN == ""
    error_message = "Cada opción se resuelve por separado."
  }
  assert {
    condition     = output.secret_arns == [var.postgres_secret_arn, var.anthropic_secret_arn]
    error_message = "La política incluye solo los secretos configurados, en orden."
  }
}

# El secreto de PostgreSQL es obligatorio.
run "postgres_obligatorio" {
  command = plan

  variables {
    postgres_secret_arn = ""
  }

  expect_failures = [var.postgres_secret_arn]
}
