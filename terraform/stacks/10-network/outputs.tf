# -----------------------------------------------------------------------------
# 10-network/outputs.tf - IDs read by later stacks via terraform_remote_state:
#   15-nat           public subnets and app route tables, keyed by AZ ID
#   30-streaming     data subnets and the MSK security group
#   35-apps / 50-*   app subnets and their security groups
# Maps are keyed by AZ ID so each AZ's NAT, subnet and route table line up.
# -----------------------------------------------------------------------------
output "vpc_id" {
  description = "The VPC ID."
  value       = aws_vpc.main.id
}

output "vpc_cidr" {
  description = "The VPC address range."
  value       = aws_vpc.main.cidr_block
}

output "az_ids" {
  description = "The three AZ IDs in use, in order."
  value       = local.az_ids
}

output "public_subnet_ids_by_az" {
  description = "Public subnet per AZ ID - where 15-nat puts each NAT gateway."
  value       = { for id, s in aws_subnet.public : id => s.id }
}

output "app_route_table_ids_by_az" {
  description = "App route table per AZ ID - 15-nat adds each AZ's default route here."
  value       = { for id, rt in aws_route_table.app : id => rt.id }
}

output "app_subnet_ids" {
  description = "App subnets (one per AZ) for Fargate tasks."
  value       = [for id in local.az_ids : aws_subnet.app[id].id]
}

output "data_subnet_ids" {
  description = "Data subnets (one per AZ) for the MSK brokers."
  value       = [for id in local.az_ids : aws_subnet.data[id].id]
}

output "security_group_ids" {
  description = "Security groups by role: apps, msk, observability."
  value = {
    apps          = aws_security_group.apps.id
    msk           = aws_security_group.msk.id
    observability = aws_security_group.observability.id
  }
}
