# A small VPC of its own: one public subnet, no NAT gateway (nothing to pay for here).

data "aws_ec2_instance_type_offerings" "selected" {
  location_type = "availability-zone"

  filter {
    name   = "instance-type"
    values = [var.instance_type]
  }
}

locals {
  # The instance and its database disk must share an availability zone.
  availability_zone = try(sort(data.aws_ec2_instance_type_offerings.selected.locations)[0], null)
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = var.name }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = { Name = var.name }
}

resource "aws_subnet" "public" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, 0)
  availability_zone = local.availability_zone
  # The instance gets the Elastic IP instead, so its address survives redeploys.
  map_public_ip_on_launch = false

  tags = { Name = "${var.name}-public" }

  lifecycle {
    precondition {
      condition     = local.availability_zone != null
      error_message = "Instance type ${var.instance_type} is not offered in ${var.aws_region}."
    }
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${var.name}-public" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "api" {
  name_prefix = "${var.name}-"
  description = "dezr API: HTTP in, everything out"
  vpc_id      = aws_vpc.main.id

  tags = { Name = var.name }

  lifecycle {
    create_before_destroy = true
  }
}

# Only the API is reachable; PostgreSQL listens on the instance's loopback interface only.
resource "aws_vpc_security_group_ingress_rule" "http" {
  for_each = toset(var.allowed_cidrs)

  security_group_id = aws_security_group.api.id
  description       = "API"
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  for_each = var.ssh_key_name == null ? toset([]) : toset(var.ssh_cidrs)

  security_group_id = aws_security_group.api.id
  description       = "SSH"
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.api.id
  description       = "Packages, Docker images, S3, SSM, Bedrock"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}
