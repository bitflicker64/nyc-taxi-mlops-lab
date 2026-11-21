# S1: the Redshift warehouse (infra/modules/redshift), and the outputs contracts/platform.yaml lists
# under terraform_outputs (redshift_cluster_id, redshift_database).
#
# No security group rule is needed for it: Dagster talks to Redshift through the Data API (HTTPS to
# AWS), never to the cluster's port, so the cluster accepts no inbound connections at all.

module "redshift" {
  source = "../modules/redshift"

  # Subnets in several AZs (Redshift's subnet group wants more than one), us-east-1e left out.
  subnet_ids = data.aws_subnets.rds.ids
  # Next to the hosts. Not required: the Data API doesn't route through the VPC.
  availability_zone = var.availability_zone
}

output "redshift_cluster_id" {
  value = module.redshift.cluster_id
}

output "redshift_database" {
  value = module.redshift.database
}
