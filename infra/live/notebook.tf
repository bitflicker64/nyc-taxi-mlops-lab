# S2: the SageMaker notebook (infra/modules/notebook). It clones this public repo (github_repo in
# terraform.tfvars), reads Redshift through the Data API as labadmin, and logs to MLflow on the platform
# host, in its own experiment: only the pipeline registers models (decision 007).

module "notebook" {
  source = "../modules/notebook"

  subnet_id = data.aws_subnet.hosts.id
  repo_url  = "https://github.com/${var.github_repo}.git"
  env = {
    MLFLOW_TRACKING_URI = "http://${module.host["platform"].private_ip}:5000"
    REDSHIFT_CLUSTER_ID = module.redshift.cluster_id
    REDSHIFT_DATABASE   = module.redshift.database
    REDSHIFT_DB_USER    = "labadmin"
  }
}

# notebook -> platform: MLflow.
resource "aws_vpc_security_group_ingress_rule" "platform_mlflow_from_notebook" {
  security_group_id            = aws_security_group.host["platform"].id
  description                  = "MLflow from the SageMaker notebook"
  ip_protocol                  = "tcp"
  from_port                    = 5000
  to_port                      = 5000
  referenced_security_group_id = module.notebook.security_group_id
}

output "notebook_name" {
  value = module.notebook.name
}
