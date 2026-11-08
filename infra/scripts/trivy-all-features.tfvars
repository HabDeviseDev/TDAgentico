# Valores ficticios para que trivy evalúe también los recursos opcionales
# (HTTPS, bucket de respaldos, avisos por correo). No es una configuración real.
domain_name           = "flota.example.com"
acme_email            = "ops@example.com"
allowed_cidrs         = ["203.0.113.10/32"]
backup_bucket_enabled = true
alarm_email           = "ops@example.com"
