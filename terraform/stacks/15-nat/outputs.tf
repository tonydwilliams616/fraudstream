# -----------------------------------------------------------------------------
# 15-nat/outputs.tf - the NAT gateways and their public addresses. The public
# IPs are what outside services would see if they ever had to allow-list us.
# -----------------------------------------------------------------------------
output "nat_gateway_ids_by_az" {
  description = "NAT gateway per AZ ID."
  value       = { for id, n in aws_nat_gateway.this : id => n.id }
}

output "nat_public_ips_by_az" {
  description = "Public IPv4 address of each AZ's NAT gateway."
  value       = { for id, e in aws_eip.nat : id => e.public_ip }
}
