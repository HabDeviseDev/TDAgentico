# Ninguna salida contiene valores secretos: solo identificadores y comandos.

locals {
  public_ip = var.create_eip ? aws_eip.app[0].public_ip : aws_instance.app.public_ip
}

output "instance_id" {
  description = "ID de la instancia EC2."
  value       = aws_instance.app.id
}

output "public_ip" {
  description = "IP pública (Elastic IP si create_eip = true). Con dominio, el registro DNS A debe apuntar aquí."
  value       = local.public_ip
}

output "portal_url" {
  description = "URL del portal (y base de la API para la app Android)."
  value       = local.https_enabled ? "https://${var.domain_name}" : "http://${local.public_ip}"
}

output "architecture" {
  description = "Arquitectura del tipo de instancia y de la AMI elegida."
  value       = local.architecture
}

output "ami_id" {
  description = "AMI de Amazon Linux 2023 usada al crear la instancia."
  value       = data.aws_ssm_parameter.al2023_ami.insecure_value
}

output "availability_zone" {
  description = "Zona de la instancia y del volumen de datos."
  value       = local.availability_zone
}

output "data_volume_id" {
  description = "Volumen EBS persistente montado en /data."
  value       = aws_ebs_volume.data.id
}

output "backup_bucket" {
  description = "Bucket de respaldos lógicos de PostgreSQL (null si está deshabilitado)."
  value       = var.backup_bucket_enabled ? aws_s3_bucket.backups[0].bucket : null
}

output "deployed_revision" {
  description = "Revisión Git configurada para el despliegue inicial."
  value       = var.git_revision
}

output "ssm_session_command" {
  description = "Abre una consola en la instancia (requiere el plugin de Session Manager)."
  value       = "aws ssm start-session --region ${var.region} --target ${aws_instance.app.id}"
}

output "deploy_command" {
  description = "Despliega la revisión configurada por SSM Run Command (cambie la revisión para actualizar o volver atrás)."
  value       = "aws ssm send-command --region ${var.region} --instance-ids ${aws_instance.app.id} --document-name AWS-RunShellScript --comment fleet-deploy --parameters 'commands=[\"/opt/fleet/bin/fleet-deploy ${var.git_revision}\"],executionTimeout=[\"3600\"]'"
}
