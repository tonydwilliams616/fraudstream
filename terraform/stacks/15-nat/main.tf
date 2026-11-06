# -----------------------------------------------------------------------------
# 15-nat/main.tf - outbound internet access for the app subnets, one NAT per AZ.
#
# What: in each AZ, an Elastic IP, a NAT gateway in that AZ's public subnet, and
#       a default route (0.0.0.0/0) in that AZ's app route table pointing at it.
# Why one per AZ: if an AZ fails, tasks in the other AZs keep their own NAT. A
#       single shared NAT would be one AZ's outage taking everyone's egress
#       with it - and traffic crossing AZs to reach it is billed too.
# What uses it: Fargate tasks calling AWS APIs over HTTPS (SageMaker runtime,
#       ECR image pulls, CloudWatch Logs, STS). S3 traffic bypasses it via the
#       free gateway endpoint in 10-network. MSK brokers (data subnets) have no
#       route here at all.
# Ephemeral: destroying this stack removes the routes too, so between sessions
#       the app subnets simply have no internet access - nothing breaks,
#       nothing bills.
# Cost: ~$0.045/hour per NAT gateway + $0.005/hour per public IPv4 address
#       = ~$0.15/hour for three, plus $0.045 per GB processed.
# -----------------------------------------------------------------------------
data "terraform_remote_state" "network" {
  backend = "s3"

  config = {
    bucket = "fs-tfstate-831547420318"
    key    = "10-network/terraform.tfstate"
    region = var.aws_region
  }
}

locals {
  name = var.project_name
  net  = data.terraform_remote_state.network.outputs
}

resource "aws_eip" "nat" {
  for_each = local.net.public_subnet_ids_by_az # keyed by AZ ID

  domain = "vpc"
  tags   = { Name = "${local.name}-nat-${each.key}" }
}

resource "aws_nat_gateway" "this" {
  for_each = local.net.public_subnet_ids_by_az

  connectivity_type = "public"
  subnet_id         = each.value
  allocation_id     = aws_eip.nat[each.key].id

  tags = { Name = "${local.name}-nat-${each.key}" }
}

resource "aws_route" "app_default" {
  for_each = local.net.app_route_table_ids_by_az

  route_table_id         = each.value
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this[each.key].id # the NAT in the SAME AZ
}
