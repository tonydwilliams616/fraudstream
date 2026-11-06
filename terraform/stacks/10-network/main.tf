# -----------------------------------------------------------------------------
# 10-network/main.tf - the VPC, its subnets and routing.
#
# Layout: three subnet tiers in each of three AZs.
#   public  /24  NAT gateways only (15-nat). Nothing else gets a public IP.
#   app     /20  Fargate tasks (simulator, consumer, Prometheus/Grafana). Large,
#                because every task gets its own IP. Egress to AWS APIs goes
#                through the NAT gateway in the SAME AZ, added by 15-nat.
#   data    /24  MSK brokers. No route to the internet at all - brokers only
#                talk to clients inside the VPC.
# Route tables: one public; one PER AZ for app (each AZ uses its own NAT, so an
# AZ outage can't cut off the others); one shared for data. 15-nat adds and
# removes the 0.0.0.0/0 routes on the app tables each session, so this stack
# never changes when NAT comes and goes.
# S3 gateway endpoint (free): S3 traffic from app and data subnets stays on the
# AWS network instead of going through NAT, which charges per GB.
# -----------------------------------------------------------------------------
data "aws_availability_zones" "selected" {
  state = "available"

  filter {
    name   = "zone-id"
    values = keys(var.az_slots)
  }
}

locals {
  name = var.project_name

  # AZ ID -> AZ name in this account, plus the AZ's fixed slot for CIDR maths.
  az_name_by_id = zipmap(data.aws_availability_zones.selected.zone_ids, data.aws_availability_zones.selected.names)
  azs = {
    for id, slot in var.az_slots : id => { slot = slot, name = local.az_name_by_id[id] }
  }
  az_ids = keys(var.az_slots) # sorted, so outputs keep a stable order

  # The /16 carved up by slot (shown for 10.40.0.0/16, slots 0-2 in use):
  #   public 10.40.0-5.0/24    data 10.40.10-15.0/24    app 10.40.16.0/20 upwards
  public_cidr = { for id, az in local.azs : id => cidrsubnet(var.vpc_cidr, 8, az.slot) }
  data_cidr   = { for id, az in local.azs : id => cidrsubnet(var.vpc_cidr, 8, 10 + az.slot) }
  app_cidr    = { for id, az in local.azs : id => cidrsubnet(var.vpc_cidr, 4, 1 + az.slot) }
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true # needed for MSK bootstrap names and VPC interface endpoints

  tags = { Name = "${local.name}-vpc" }
}

# No rules = nothing can use the default security group. Every resource gets
# an explicit, named group instead (security-groups.tf).
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-default-unused" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-igw" }
}

# --- public tier --------------------------------------------------------------
resource "aws_subnet" "public" {
  for_each = local.azs

  vpc_id                  = aws_vpc.main.id
  cidr_block              = local.public_cidr[each.key]
  availability_zone_id    = each.key
  map_public_ip_on_launch = false # NAT gateways use Elastic IPs; nothing else is public

  tags = { Name = "${local.name}-public-${each.value.name}", Tier = "public" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-public" }
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
}

resource "aws_route_table_association" "public" {
  for_each = aws_subnet.public

  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

# --- app tier -----------------------------------------------------------------
resource "aws_subnet" "app" {
  for_each = local.azs

  vpc_id               = aws_vpc.main.id
  cidr_block           = local.app_cidr[each.key]
  availability_zone_id = each.key

  tags = { Name = "${local.name}-app-${each.value.name}", Tier = "app" }
}

resource "aws_route_table" "app" {
  for_each = local.azs

  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-app-${each.value.name}" }
}

resource "aws_route_table_association" "app" {
  for_each = aws_subnet.app

  subnet_id      = each.value.id
  route_table_id = aws_route_table.app[each.key].id
}

# --- data tier ----------------------------------------------------------------
resource "aws_subnet" "data" {
  for_each = local.azs

  vpc_id               = aws_vpc.main.id
  cidr_block           = local.data_cidr[each.key]
  availability_zone_id = each.key

  tags = { Name = "${local.name}-data-${each.value.name}", Tier = "data" }
}

resource "aws_route_table" "data" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-data" }
}

resource "aws_route_table_association" "data" {
  for_each = aws_subnet.data

  subnet_id      = each.value.id
  route_table_id = aws_route_table.data.id
}

# --- S3 gateway endpoint (free) -------------------------------------------------
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = concat([for rt in aws_route_table.app : rt.id], [aws_route_table.data.id])

  tags = { Name = "${local.name}-s3" }
}
