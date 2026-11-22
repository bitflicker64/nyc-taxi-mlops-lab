#!/usr/bin/env bash
# Bring the lab back after a Learner Lab session end (S4, decision 012). Run it after Start Lab and
# after exporting the new session's credentials:
#
#   scripts/restore.sh
#
# It starts only what the lab already had and was stopped (hosts, RDS, Redshift), then waits, in the
# order things depend on each other: RDS -> MLflow -> serving loads the champion -> the status run.
# Every step checks first, so running it twice is safe. It prints how long each step took.
#
# It never does these on its own (decision 012); it prints the command instead:
#   - change the firewall (terraform apply for a new laptop IP)
#   - start the notebook (only exploration needs it, and it costs money while it runs)
#   - move a model alias (only promote.yaml moves champion)
#   - deploy an app or serving image (restore brings back what ran, it doesn't ship anything)
#
# Exit status: health.sh's (0 when every piece is OK).
set -euo pipefail
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
root="$(cd "$(dirname "$0")/.." && pwd)"
repo=$(gh repo view --json nameWithOwner -q .nameWithOwner)
t0=$(date +%s)

step() { printf '[%4ss] %s\n' "$(($(date +%s) - t0))" "$*"; }
die() { step "STOP: $*" >&2; exit 1; }

# wait_for DESCRIPTION TIMEOUT_SECONDS COMMAND...: run COMMAND every 10 s until it succeeds.
wait_for() {
  local what=$1 timeout=$2 start
  shift 2
  start=$(date +%s)
  until "$@" >/dev/null 2>&1; do
    (($(date +%s) - start < timeout)) || return 1
    sleep 10
  done
  step "$what ($(($(date +%s) - start)) s)"
}

# on_host ROLE COMMAND: run COMMAND on a lab host over SSM; prints its stdout, fails if it fails.
on_host() {
  local id cmd request status
  id=$(terraform -chdir="$root/infra/live" output -raw "$1_instance_id")
  request=$(mktemp) && chmod 600 "$request"
  jq -n --arg id "$id" --arg c "$2" \
    '{InstanceIds: [$id], DocumentName: "AWS-RunShellScript", Parameters: {commands: [$c]}, TimeoutSeconds: 60}' >"$request"
  cmd=$(aws ssm send-command --cli-input-json "file://$request" --query Command.CommandId --output text)
  rm -f "$request"
  for _ in $(seq 1 30); do
    sleep 2
    status=$(aws ssm get-command-invocation --command-id "$cmd" --instance-id "$id" --query Status --output text 2>/dev/null) || continue
    case $status in
      Success) aws ssm get-command-invocation --command-id "$cmd" --instance-id "$id" --query StandardOutputContent --output text; return 0 ;;
      Failed | Cancelled | TimedOut) return 1 ;;
    esac
  done
  return 1
}

# 1. Session and laptop: only report. A firewall change is the student's call.
aws sts get-caller-identity >/dev/null 2>&1 ||
  die "AWS credentials don't work: Start Lab, then paste this session's into ~/.aws/credentials"
step "credentials OK"
ip=$(curl -fsS -m 5 https://checkip.amazonaws.com || echo unknown)
if ! grep -q "\"$ip/32\"" "$root/infra/live/terraform.tfvars" 2>/dev/null; then
  step "NOTE: your IP $ip isn't in ingress_cidrs. To reach the app from here, add \"$ip/32\" to"
  step "      infra/live/terraform.tfvars and run: terraform -chdir=infra/live apply (not done for you)"
fi

# 2. Hosts. Start Lab starts them; this covers a host someone stopped by hand.
hosts() { # STATES: the lab's instance ids in those states, space-separated
  aws ec2 describe-instances --filters Name=tag:project,Values=mlops-lab "Name=instance-state-name,Values=$1" \
    --query 'Reservations[].Instances[].InstanceId' --output text
}
stopped=$(hosts stopped)
if [[ -n $stopped ]]; then
  step "starting stopped hosts: $stopped"
  # shellcheck disable=SC2086 # one argument per id
  aws ec2 start-instances --instance-ids $stopped >/dev/null
fi
# shellcheck disable=SC2046
aws ec2 wait instance-running --instance-ids $(hosts pending,running,stopping,stopped)
step "hosts running"

# 3. RDS and Redshift: start both, then wait for both, since each takes minutes on its own.
rds() { aws rds describe-db-instances --db-instance-identifier lab --query 'DBInstances[0].DBInstanceStatus' --output text; }
redshift() { aws redshift describe-clusters --cluster-identifier lab --query 'Clusters[0].ClusterStatus' --output text; }
rds_is() { [[ $(rds) == "$1" ]]; }
redshift_is() { [[ $(redshift) == "$1" ]]; }
rds_isnt() { ! rds_is "$1"; }
redshift_isnt() { ! redshift_is "$1"; }
# A stop or pause still in progress has to finish before AWS accepts a start.
wait_for "RDS not stopping" 900 rds_isnt stopping || die "RDS is still stopping after 15 min"
wait_for "Redshift not pausing" 900 redshift_isnt pausing || die "Redshift is still pausing after 15 min"
if rds_is stopped; then
  step "starting RDS (~7 min)"
  aws rds start-db-instance --db-instance-identifier lab >/dev/null
fi
if redshift_is paused; then
  step "resuming Redshift (~3 min)"
  aws redshift resume-cluster --cluster-identifier lab >/dev/null
fi
wait_for "RDS available" 1200 rds_is available ||
  die "RDS isn't available after 20 min: aws rds describe-events --source-identifier lab --source-type db-instance"
wait_for "Redshift available" 1200 redshift_is available ||
  die "Redshift isn't available after 20 min: aws redshift describe-events --source-identifier lab --source-type cluster"

# 4. Runners: the services start them at boot; they need the host's network up first.
runners_online() {
  [[ $(gh api "repos/$repo/actions/runners" -q '[.runners[] | select(.name == "lab-app" or .name == "lab-platform") | .status] | map(select(. == "online")) | length') == 2 ]]
}
wait_for "runners lab-app and lab-platform online" 600 runners_online ||
  die "a runner is offline: gh api repos/$repo/actions/runners; on the host, sudo ~/actions-runner/svc.sh status"

# 5. MLflow comes back by itself (restart: unless-stopped) once RDS answers; nothing to start, only wait.
wait_for "MLflow answers on the platform host" 900 on_host platform "curl -fsS -m 5 http://127.0.0.1:5000/health" ||
  die "MLflow isn't answering: on the platform host, docker ps -a and docker logs platform-mlflow-1"

# 6. Serving retries loading its alias every 30 s until it has one, so riders get the champion again
#    without a reload. Checked through Traefik on the app host, the way a rider's request goes.
champion_served() {
  on_host app "curl -fsS -m 5 -X POST http://127.0.0.1/predict -H 'Content-Type: application/json' -d '{\"pickup_zone_id\": 132, \"dropoff_zone_id\": 230, \"pickup_datetime\": \"2024-03-08T17:05:00\"}'" |
    jq -e '.model.alias == "champion"'
}
wait_for "riders get the champion again" 300 champion_served ||
  die "still the fallback: on the app host, deploy/web.sh status, then deploy/web.sh reload champion"

# 7. The status run: what the grader sees.
since=$(date -u +%FT%TZ)
gh workflow run status.yaml --repo "$repo" >/dev/null
status_run() { gh run list --repo "$repo" --workflow status.yaml --created ">=$since" --limit 1 --json databaseId -q '.[0].databaseId' | grep -q .; }
wait_for "status run started" 120 status_run || die "status.yaml didn't start: is vars.LAB_PLATFORM on?"
run=$(gh run list --repo "$repo" --workflow status.yaml --created ">=$since" --limit 1 --json databaseId -q '.[0].databaseId')
gh run watch "$run" --repo "$repo" --exit-status >/dev/null || die "status run failed: gh run view $run --repo $repo --log-failed"
step "status run green"

# 8. The same check you'd run by hand.
step "health.sh:"
"$root/scripts/health.sh"
