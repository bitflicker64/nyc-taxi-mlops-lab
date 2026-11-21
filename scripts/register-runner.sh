#!/usr/bin/env bash
# Register a lab host as a self-hosted GitHub Actions runner, named and labelled lab-<role>
# (contracts/runtime.yaml), running as a systemd service so it comes back after Start Lab.
#
#   export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_SESSION_TOKEN=...   (AWS Details)
#   scripts/register-runner.sh app        (S0; platform in S1, llm in S6)
#
# Runner credentials (decision record in decisions/): jobs use the host's instance profile
# (LabInstanceProfile through IMDS), so nothing on the host or in GitHub holds AWS keys.
#
# No SSH in this lab: the install runs through SSM Run Command. The registration token is
# short-lived (one hour) and never printed; it is visible only in this account's SSM command
# history until it expires. Safe to re-run: an already configured, running runner is left alone.
set -euo pipefail
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""

role=${1:?usage: scripts/register-runner.sh app|platform|llm}
case $role in app | platform | llm) ;; *) echo "role must be app, platform or llm" >&2; exit 1 ;; esac
name="lab-$role"

# Pinned runner release and its published SHA-256 checksums (github.com/actions/runner/releases).
runner_version=2.338.2
sha_arm64=670adb2f268fe21dcbb58b3db0e0aa9d5b3bddfe2c651481336e561ff86de945
sha_x64=baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40

root="$(cd "$(dirname "$0")/.." && pwd)"
repo=$(gh repo view --json nameWithOwner -q .nameWithOwner)
aws sts get-caller-identity >/dev/null ||
  { echo "AWS credentials missing or expired: export them from Learner Lab > AWS Details" >&2; exit 1; }

# The repo is public: a fork's PR must not run anything before you approve it, and this runner is
# about to hold LabRole. Refuse unless approval is required for all external contributors.
policy=$(gh api "repos/$repo/actions/permissions/fork-pr-contributor-approval" -q .approval_policy 2>/dev/null) || policy=""
if [[ -z $policy ]]; then
  echo "could not read the fork-PR approval setting of $repo." >&2
  read -r -p "Is Settings > Actions > General set to 'Require approval for all external contributors'? [y/N] " ok
  [[ $ok == [yY] ]] || exit 1
elif [[ $policy != all_external_contributors ]]; then
  echo "refusing: fork-PR approval is '$policy'. Set Settings > Actions > General >" >&2
  echo "'Require approval for all external contributors' first, then re-run." >&2
  exit 1
fi

instance=$(terraform -chdir="$root/infra/live" output -raw "${role}_instance_id")

echo "waiting for $name ($instance) to be online in SSM"
for _ in $(seq 60); do
  ping=$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$instance" \
    --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || true)
  [[ $ping == Online ]] && break
  sleep 10
done
[[ $ping == Online ]] || { echo "$instance is not online in SSM after 10 minutes (is the host running?)" >&2; exit 1; }

token=$(gh api -X POST "repos/$repo/actions/runners/registration-token" -q .token)

# Runs as root on the host. Waits for user-data (Docker, the runner user) to finish first.
# read -d '' rather than $(cat <<EOF): bash misparses the case patterns' ")" inside $( ).
IFS= read -r -d '' remote <<EOF || true
set -euo pipefail
for _ in \$(seq 60); do [ -f /var/lib/lab-setup-done ] && break; sleep 5; done
[ -f /var/lib/lab-setup-done ] || { echo "user-data has not finished: see /var/log/lab-setup.log" >&2; exit 1; }
cd /opt/actions-runner
# svc.sh writes the systemd unit's name to .service.
if [ -f .runner ] && [ -f .service ] && systemctl is-active --quiet "\$(cat .service)"; then
  echo "runner already configured and running"; exit 0
fi
case \$(uname -m) in
  aarch64) arch=arm64; sum=$sha_arm64 ;;
  x86_64) arch=x64; sum=$sha_x64 ;;
  *) echo "unsupported architecture \$(uname -m)" >&2; exit 1 ;;
esac
tarball=actions-runner-linux-\$arch-$runner_version.tar.gz
if [ ! -x ./config.sh ]; then
  curl -fsSL -o "/tmp/\$tarball" "https://github.com/actions/runner/releases/download/v$runner_version/\$tarball"
  echo "\$sum  /tmp/\$tarball" | sha256sum -c -
  sudo -u runner tar xzf "/tmp/\$tarball" -C /opt/actions-runner
  rm -f "/tmp/\$tarball"
fi
sudo -u runner ./config.sh --unattended --replace --url "https://github.com/$repo" \
  --token "$token" --name "$name" --labels "$name" --work _work
./svc.sh install runner
./svc.sh start
echo "runner $name installed and started"
EOF
unset token

# The request goes through a private temp file, not the command line (where `ps` shows it) or
# stdin (the CLI can't read file:///dev/stdin from a pipe on macOS). Removed on exit.
request=$(mktemp)
trap 'rm -f "$request"' EXIT
chmod 600 "$request"
jq -n --arg id "$instance" --arg cmd "$remote" --arg name "$name" '{
  DocumentName: "AWS-RunShellScript",
  InstanceIds: [$id],
  Comment: ("register GitHub runner " + $name),
  TimeoutSeconds: 900,
  Parameters: {commands: [$cmd], executionTimeout: ["900"]}
}' >"$request"
unset remote
command_id=$(aws ssm send-command --cli-input-json "file://$request" --query Command.CommandId --output text)
rm -f "$request"

echo "installing the runner on $name (SSM command $command_id)"
aws ssm wait command-executed --command-id "$command_id" --instance-id "$instance" || true
read -r status out err < <(aws ssm get-command-invocation --command-id "$command_id" --instance-id "$instance" \
  --query '[Status, StandardOutputContent, StandardErrorContent]' --output json | jq -r 'map(@base64) | join(" ")')
printf '%s\n' "$(base64 -d <<<"$out")"
if [[ $(base64 -d <<<"$status") != Success ]]; then
  base64 -d <<<"$err" >&2
  echo "runner install failed on $name" >&2
  exit 1
fi

echo "waiting for $name to show as online on GitHub"
for _ in $(seq 30); do
  state=$(gh api "repos/$repo/actions/runners" -q ".runners[] | select(.name == \"$name\") | .status" 2>/dev/null || true)
  [[ $state == online ]] && { echo "$name is online"; exit 0; }
  sleep 5
done
echo "$name registered but not online yet: check 'systemctl status actions.runner.*' on the host (SSM)" >&2
exit 1
