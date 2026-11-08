# Monitoreo básico:
# - Métricas estándar de EC2 (CPU, chequeos de estado).
# - Métricas propias que publica /opt/fleet/bin/fleet-health cada minuto
#   (systemd timer): ServicesHealthy, DataDiskUsedPercent, RootDiskUsedPercent,
#   MemoryUsedPercent en el espacio de nombres Fleet/<proyecto>-<entorno>.
# - Avisos por correo opcionales (SNS) si se define alarm_email.

locals {
  metric_ns     = "Fleet/${local.name}"
  alarm_actions = var.alarm_email == null ? [] : [aws_sns_topic.alarms[0].arn]
}

resource "aws_sns_topic" "alarms" {
  count = var.alarm_email == null ? 0 : 1
  name  = "${local.name}-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == null ? 0 : 1
  topic_arn = aws_sns_topic.alarms[0].arn
  protocol  = "email"
  endpoint  = var.alarm_email # requiere confirmar el correo que envía AWS
}

# Falla del host físico: AWS recupera la instancia (misma ID, IP y volúmenes).
resource "aws_cloudwatch_metric_alarm" "system_status" {
  alarm_name          = "${local.name}-system-status"
  alarm_description   = "Chequeo de sistema fallido: se recupera la instancia automáticamente."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_System"
  dimensions          = { InstanceId = aws_instance.app.id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  alarm_actions       = concat(["arn:${data.aws_partition.current.partition}:automate:${var.region}:ec2:recover"], local.alarm_actions)
}

resource "aws_cloudwatch_metric_alarm" "instance_status" {
  alarm_name          = "${local.name}-instance-status"
  alarm_description   = "Chequeo de instancia fallido (sistema operativo sin respuesta)."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_Instance"
  dimensions          = { InstanceId = aws_instance.app.id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "cpu" {
  alarm_name          = "${local.name}-cpu"
  alarm_description   = "CPU sostenida alta (en t3 puede agotar créditos)."
  namespace           = "AWS/EC2"
  metric_name         = "CPUUtilization"
  dimensions          = { InstanceId = aws_instance.app.id }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = 85
  alarm_actions       = local.alarm_actions
}

# Sin datos = el script de salud no corre o la instancia no publica: alarma.
resource "aws_cloudwatch_metric_alarm" "services" {
  alarm_name          = "${local.name}-services"
  alarm_description   = "Algún servicio de Compose no está en ejecución o no está sano."
  namespace           = local.metric_ns
  metric_name         = "ServicesHealthy"
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 3
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = "breaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "data_disk" {
  alarm_name          = "${local.name}-data-disk"
  alarm_description   = "Volumen de datos (/data: Kafka, TimescaleDB, respaldos) sobre 80 %."
  namespace           = local.metric_ns
  metric_name         = "DataDiskUsedPercent"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 80
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "root_disk" {
  alarm_name          = "${local.name}-root-disk"
  alarm_description   = "Disco raíz (imágenes Docker, registros) sobre 85 %."
  namespace           = local.metric_ns
  metric_name         = "RootDiskUsedPercent"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 85
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "memory" {
  alarm_name          = "${local.name}-memory"
  alarm_description   = "Memoria usada sobre 90 % durante 10 minutos."
  namespace           = local.metric_ns
  metric_name         = "MemoryUsedPercent"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  comparison_operator = "GreaterThanThreshold"
  threshold           = 90
  alarm_actions       = local.alarm_actions
}
