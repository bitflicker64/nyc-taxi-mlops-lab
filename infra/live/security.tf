# S0: security groups. One per host plus one for RDS; every inbound rule is a port named in
# contracts/runtime.yaml. Host-to-host rules reference security groups, not IPs, so they survive a
# private IP change. No port 22 anywhere: shell is SSM Session Manager, UIs are SSM port forwarding.
#
# Ingress scope (decision record in decisions/): port 80 on the app host is open only to
# var.ingress_cidrs, set in terraform.tfvars.

resource "aws_security_group" "host" {
  for_each = local.hosts

  name        = "lab-${each.key}"
  description = "lab-${each.key} host"
  vpc_id      = data.aws_vpc.default.id
  tags        = { Name = "lab-${each.key}" }
}

resource "aws_security_group" "rds" {
  name        = "lab-rds"
  description = "lab Postgres"
  vpc_id      = data.aws_vpc.default.id
  tags        = { Name = "lab-rds" }
}

# Outbound for every host: image pulls (GHCR, Docker Hub), the runner's long poll to GitHub, the SSM
# agent, AWS APIs. All of it is HTTPS initiated from the host, so it needs no inbound rule.
resource "aws_vpc_security_group_egress_rule" "host_all" {
  for_each = local.hosts

  security_group_id = aws_security_group.host[each.key].id
  description       = "all outbound"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# Traefik on the app host, from the CIDRs you chose.
resource "aws_vpc_security_group_ingress_rule" "app_http" {
  for_each = toset(var.ingress_cidrs)

  security_group_id = aws_security_group.host["app"].id
  description       = "Traefik (HTTP) from ${each.value}"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = each.value
}

# app -> platform: MLflow.
resource "aws_vpc_security_group_ingress_rule" "platform_mlflow_from_app" {
  security_group_id            = aws_security_group.host["platform"].id
  description                  = "MLflow from the app host"
  ip_protocol                  = "tcp"
  from_port                    = 5000
  to_port                      = 5000
  referenced_security_group_id = aws_security_group.host["app"].id
}

# app, platform -> llm: chat (8080) and embeddings (8081).
locals {
  llm_clients = {
    for pair in setproduct(["app", "platform"], [8080, 8081]) : "${pair[0]}-${pair[1]}" => {
      from = pair[0]
      port = pair[1]
    }
  }
  rds_clients = toset(["app", "platform"])
}

resource "aws_vpc_security_group_ingress_rule" "llm" {
  for_each = local.llm_clients

  security_group_id            = aws_security_group.host["llm"].id
  description                  = "LLM port ${each.value.port} from the ${each.value.from} host"
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
  referenced_security_group_id = aws_security_group.host[each.value.from].id
}

# app, platform -> rds: Postgres. RDS needs no outbound rule: replies to these connections are
# allowed by the security group's state tracking.
resource "aws_vpc_security_group_ingress_rule" "rds" {
  for_each = local.rds_clients

  security_group_id            = aws_security_group.rds.id
  description                  = "Postgres from the ${each.key} host"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.host[each.key].id
}
