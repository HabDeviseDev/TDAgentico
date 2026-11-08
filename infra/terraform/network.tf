# Red mínima: una VPC, una subred pública en una zona, salida a Internet por
# Internet Gateway (para Git, imágenes, Secrets Manager, SSM y Let's Encrypt).

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${local.name}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-igw" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidr
  availability_zone       = local.availability_zone
  map_public_ip_on_launch = false # la IP pública la da la EIP o la instancia explícitamente
  tags                    = { Name = "${local.name}-public-${local.availability_zone}" }

  lifecycle {
    precondition {
      condition     = local.availability_zone != null
      error_message = "Ninguna zona de la región ofrece instance_type: elija otro tipo o fije availability_zone."
    }
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-public" }
}

resource "aws_route" "internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# El grupo por defecto de la VPC queda sin reglas (nada lo usa).
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-default-sin-reglas" }
}

# Único punto de entrada: Caddy (portal y proxy). Sin SSH: la administración
# es por Systems Manager. Kafka, PostgreSQL, API y agente no publican puertos.
resource "aws_security_group" "app" {
  name        = "${local.name}-app"
  description = "Portal ${local.name}: HTTP/HTTPS desde CIDR autorizados"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "${local.name}-app" }
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  for_each          = local.https_enabled ? toset(var.allowed_cidrs) : toset([])
  security_group_id = aws_security_group.app.id
  description       = "Portal HTTPS desde CIDR autorizado"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = each.value
}

# Sin dominio: el portal se sirve por HTTP solo a los CIDR autorizados.
resource "aws_vpc_security_group_ingress_rule" "http_restricted" {
  for_each          = local.https_enabled ? toset([]) : toset(var.allowed_cidrs)
  security_group_id = aws_security_group.app.id
  description       = "Portal HTTP desde CIDR autorizado (sin HTTPS)"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = each.value
}

# Con dominio: el puerto 80 queda abierto para el desafío HTTP-01 de
# Let's Encrypt. Caddy solo responde ahí el desafío y la redirección a HTTPS;
# el portal se sirve únicamente por 443, limitado a allowed_cidrs.
resource "aws_vpc_security_group_ingress_rule" "acme_http" {
  count             = local.https_enabled ? 1 : 0
  security_group_id = aws_security_group.app.id
  description       = "Desafio HTTP-01 de Let's Encrypt y redireccion a HTTPS"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

# Salida solo por 443. Los destinos (GitHub, Docker Hub, Let's Encrypt, APIs
# de AWS y del proveedor del modelo) no tienen rangos fijos; sin NAT ni VPC
# endpoints no se puede acotar más (excepción documentada en .trivyignore.yaml).
# Los repositorios de Amazon Linux 2023 también van por HTTPS.
resource "aws_vpc_security_group_egress_rule" "https_out" {
  security_group_id = aws_security_group.app.id
  description       = "Salida HTTPS: Git, imagenes, Secrets Manager, SSM, CloudWatch, Let's Encrypt"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "dns_out" {
  security_group_id = aws_security_group.app.id
  description       = "DNS de la VPC"
  ip_protocol       = "udp"
  from_port         = 53
  to_port           = 53
  cidr_ipv4         = "${cidrhost(var.vpc_cidr, 2)}/32"
}

resource "aws_vpc_security_group_egress_rule" "ntp_out" {
  security_group_id = aws_security_group.app.id
  description       = "Amazon Time Sync"
  ip_protocol       = "udp"
  from_port         = 123
  to_port           = 123
  cidr_ipv4         = "169.254.169.123/32"
}
