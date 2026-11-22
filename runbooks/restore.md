# Runbook: bring the lab back after a Learner Lab session end

Owner: Himanshu Verma · Last run: 2026-11-22 · Time to restore last run: 4 min 11 s (`scripts/restore.sh`, Redshift paused)

## When to use it

- "The app link doesn't open." Or the app quotes 12.25 minutes for every trip.
- `aws` says `explicit deny ... voc-cancel-cred`.
- The GitHub runners show offline.
- Basically, any time you come back to the lab after the session timer ran out, or after you stopped things for the night.

## Before you start

- **Learner Lab:** click **Start Lab** and wait for the green dot. That also starts the EC2 hosts.
- **AWS credentials:** in Learner Lab, open **AWS Details → AWS CLI**, copy the block, then run `pbpaste > ~/.aws/credentials && chmod 600 ~/.aws/credentials`.
- **gh:** logged in as the repo owner.
- **Terraform:** `infra/live` initialised, because the scripts read its outputs.

## Steps

The first five rows are from the real session end on 2026-11-22: hosts stopped at the 4-hour mark, then Start Lab at 10:28:59 UTC. The last rows are from S4's prove-it test: pause Redshift, reboot the platform host, touch nothing.

| # | Check (command) | Healthy looks like | If not: fix (command) | Took | Automated? |
| --- | --- | --- | --- | --- | --- |
| 1 | `aws sts get-caller-identity` | your `voclabs/...` ARN | Start Lab, then paste the new credentials (above) | ~1 min (by hand) | no: only Learner Lab's page hands them out. `restore.sh` stops here with this hint |
| 2 | `scripts/health.sh`, lines `app`, `platform`, `llm` | `running` | Start Lab starts them; a host stopped by hand: `aws ec2 start-instances --instance-ids <id>` | 0 (Start Lab) | yes: `restore.sh` starts any stopped lab host |
| 3 | `docker ps` on each host (SSM) | every container `Up (healthy)` | nothing: `restart: unless-stopped` brings them back at boot | < 1 min after boot | by itself (Docker restart policy) |
| 4 | `curl -X POST http://<app_public_ip>/predict ...` | `"alias": "champion"` | wait: serving retries loading its alias every 30 s; stuck: `deploy/web.sh reload champion` on the app host | 53 s after boot (champion `loaded_at` 10:29:52) | by itself (serving's retry); `restore.sh` waits for it |
| 5 | `health.sh` line `laptop-ip` | `<ip> is in ingress_cidrs` | add `"<ip>/32"` to `infra/live/terraform.tfvars`, `terraform -chdir=infra/live plan`, then `apply` | ~2 min (plan + apply, 1 rule added) | no, on purpose (decision 012). Kept both home and college IPs, so moving between them needs no apply |
| 6 | `health.sh` line `status` | `Dagster answered the status run N min ago`, N ≤ 40 | `gh workflow run status.yaml`; its artifact must show `"rds": "available", "redshift": "available"` | 23 s | yes: `restore.sh` runs it and waits for green |
| 7 | GitHub → Settings → Actions → Runners | `lab-app`, `lab-platform` online | nothing: the runner services start at boot. Stuck: `sudo ~/actions-runner/svc.sh status` on the host | back with the hosts | by itself (systemd); `restore.sh` waits for it |
| 8 | `health.sh` line `redshift` | `available` | `aws redshift resume-cluster --cluster-identifier lab` | 187 s | yes: `restore.sh` |
| 9 | platform host after `aws ec2 reboot-instances`: MLflow `:5000/health`, Dagster `:3000/server_info` | both 200 | nothing | ≤ 62 s after the reboot; riders kept the champion throughout (serving holds the model in memory) | by itself (Docker restart policy) |
| 10 | `health.sh` line `rds` | `available` | `aws rds start-db-instance --db-instance-identifier lab` (~7 min, per health.sh) | not measured: RDS was never stopped this time (no RDS events in 24 h) | yes: `restore.sh` starts it, and waits before MLflow |
| 11 | Did end of day happen? `aws redshift describe-events`, `aws rds describe-events` | RDS stopped, Redshift paused, notebook stopped | at the end of the day: `aws redshift pause-cluster ...`, `aws rds stop-db-instance ...`, `aws sagemaker stop-notebook-instance ...` | — | no: stopping is a choice about cost and about who still needs it. Found Redshift running since 04:43 UTC and RDS never stopped |
| 12 | `health.sh` line `notebook` | `Stopped` is fine | when you need it: `aws sagemaker start-notebook-instance --notebook-instance-name lab` (~6 min) | — | no, on purpose: only exploration needs it, and it costs money while it runs |

## Done when

From a rider's point of view first:

1. `curl -X POST http://<app_public_ip>/predict -H 'Content-Type: application/json' -d '{"pickup_zone_id": 132, "dropoff_zone_id": 230, "pickup_datetime": "2024-03-08T17:05:00"}'` answers with `"alias": "champion"`.
2. `scripts/health.sh` exits 0.
3. The latest `status.yaml` run is green, and its artifact reports RDS and Redshift `available`.

## What we automated, and what we left as a step

See [decision 012](../decisions/012-restore-automation.md). `scripts/restore.sh` does rows 2, 6, 8 and 10. It waits for rows 3, 4, 7 and 9, which come back by themselves, in dependency order: RDS, then MLflow, then serving's champion.

A person still does these:

- **Row 1, credentials:** only Learner Lab's page hands them out.
- **Row 5, firewall:** a new IP changes who can reach the app. Read the plan, then apply it yourself.
- **Row 11, end of day:** stopping things trades cost against who still needs them that evening.
- **Row 12, notebook:** costs money and is rarely needed.
- **Never in a restore:** moving a model alias (only `promote.yaml`, with approval) or deploying an image (CD's job).
