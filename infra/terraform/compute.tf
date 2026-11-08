resource "aws_instance" "app" {
  ami                    = data.aws_ssm_parameter.al2023_ami.value
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name
  # IP pública para salir a Internet; la dirección estable es la EIP.
  associate_public_ip_address = true
  monitoring                  = false # métricas básicas de 5 min (sin costo adicional)
  disable_api_termination     = false

  user_data_base64            = base64gzip(local.user_data)
  user_data_replace_on_change = false # cambiar la revisión NO reemplaza la instancia: se redespliega por SSM

  metadata_options {
    http_tokens                 = "required" # IMDSv2
    http_put_response_hop_limit = 2          # contenedores en bridge no deben usar las credenciales
    http_endpoint               = "enabled"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_size_gb
    encrypted             = true
    kms_key_id            = var.data_volume_kms_key_arn
    delete_on_termination = true
  }

  volume_tags = merge(local.tags, { Name = "${local.name}-root" })

  tags = { Name = "${local.name}-app" }

  lifecycle {
    # La AMI más reciente cambia con el tiempo: no reemplazar la instancia por eso.
    ignore_changes = [ami, user_data_base64]

    precondition {
      condition     = var.domain_name == null || var.acme_email != null
      error_message = "Con domain_name se requiere acme_email (cuenta de Let's Encrypt)."
    }
    precondition {
      condition     = contains(data.aws_ec2_instance_type.selected.supported_architectures, "arm64") || contains(data.aws_ec2_instance_type.selected.supported_architectures, "x86_64")
      error_message = "El tipo de instancia debe ser x86_64 o arm64."
    }
  }
}

resource "aws_eip" "app" {
  count    = var.create_eip ? 1 : 0
  domain   = "vpc"
  instance = aws_instance.app.id
  tags     = { Name = "${local.name}-eip" }

  depends_on = [aws_internet_gateway.main]
}

locals {
  user_data = templatefile("${path.module}/templates/user_data.sh.tftpl", {
    deploy_env      = local.deploy_env
    compose_version = var.compose_version
    buildx_version  = var.buildx_version
    architecture    = local.architecture
    auto_deploy     = var.auto_deploy
    # Scripts planos (revisados con shellcheck) incrustados en base64.
    install_docker_b64 = filebase64("${path.module}/templates/install-docker.sh")
    fleet_deploy_b64   = filebase64("${path.module}/templates/fleet-deploy.sh")
  })
}
