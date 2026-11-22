# S0: the root module. Three hosts (contracts/runtime.yaml), Postgres, the artifact bucket and the
# lab/wiki secret container, all in the account's default VPC.

# The default VPC: Learner Lab accounts come with one, and this lab needs no NAT gateway (not allowed)
# or private subnets. Hosts get public IPs for outbound HTTPS (GHCR, GitHub, SSM); inbound is the
# security groups' job (security.tf).
data "aws_vpc" "default" {
  default = true
}

# The hosts share one AZ (variables.tf: us-east-1e lacks Graviton types).
data "aws_subnet" "hosts" {
  vpc_id            = data.aws_vpc.default.id
  availability_zone = var.availability_zone
  default_for_az    = true
}

# RDS wants a subnet group spanning at least two AZs, even for a single-AZ instance. us-east-1e is
# left out for the same reason as above: db.t4g isn't offered there.
data "aws_subnets" "rds" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
  filter {
    name   = "availability-zone"
    values = ["us-east-1a", "us-east-1b", "us-east-1c", "us-east-1d", "us-east-1f"]
  }
}

locals {
  hosts = {
    platform = var.platform_instance_type
    app      = var.app_instance_type
    llm      = var.llm_instance_type
  }
}

module "host" {
  source   = "../modules/host"
  for_each = local.hosts

  role               = each.key
  instance_type      = each.value
  subnet_id          = data.aws_subnet.hosts.id
  security_group_ids = [aws_security_group.host[each.key].id]
}

module "rds" {
  source = "../modules/rds"

  instance_class     = var.rds_instance_class
  subnet_ids         = data.aws_subnets.rds.ids
  security_group_ids = [aws_security_group.rds.id]
}

# MLflow artifacts and training sets (runtime.yaml: LAB_ARTIFACT_BUCKET). The module appends the
# account id, so the name is globally unique.
module "artifacts" {
  source = "../modules/bucket"
  name   = "lab-artifacts"
}

# Container only: the value is put out of band in S6 (runtime.yaml: secrets.wiki).
module "wiki_secret" {
  source      = "../modules/secret"
  name        = "lab/wiki"
  description = "Wiki URL and token for the support assistant (S6). Value set outside Terraform."
}

# Grafana Cloud remote write (S5): {"url", "user", "token"}. Alloy on each host reads it with the
# instance profile; the value is put from the laptop, so it's never in Terraform state or GitHub.
module "grafana_secret" {
  source      = "../modules/secret"
  name        = "lab/grafana"
  description = "Grafana Cloud remote-write URL, user and token for Alloy (S5). Value set outside Terraform."
}
