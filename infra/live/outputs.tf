# S0: the outputs contracts/runtime.yaml lists under terraform_outputs. scripts/register-runner.sh,
# scripts/catch-up.sh and the grader read them by these names.

output "app_public_ip" {
  description = "Elastic IP of the app host: Traefik on port 80."
  value       = module.host["app"].public_ip
}

output "app_instance_id" {
  value = module.host["app"].instance_id
}

output "platform_instance_id" {
  value = module.host["platform"].instance_id
}

output "llm_instance_id" {
  value = module.host["llm"].instance_id
}

output "rds_endpoint" {
  description = "Postgres host name. The password is in Secrets Manager (lab/rds), never in state."
  value       = module.rds.endpoint
}

output "artifact_bucket" {
  value = module.artifacts.name
}
