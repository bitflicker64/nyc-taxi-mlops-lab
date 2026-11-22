# 012: A laptop restore script brings back what was stopped; it never changes access, cost or versions

Stage: S4 · Date: 2026-11-22 · Author: Himanshu Verma · Drafted by agent: yes

## Choice

`scripts/restore.sh`, run by hand from the laptop after Start Lab and pasting new credentials. It:

- starts what the lab already had and that was stopped: hosts (if someone stopped one by hand), RDS, and Redshift. It starts RDS and Redshift together, then waits for both;
- waits, in dependency order, for things that come back by themselves: the runners, MLflow (`restart: unless-stopped`, once RDS answers), and serving loading the champion (it retries every 30 s);
- runs `status.yaml`, then `scripts/health.sh`, and exits with health.sh's status;
- prints how long each step took, so the runbook's times come from the script's own output.

It never does these on its own. It prints the command instead:

- **Change the firewall.** A new laptop IP needs `terraform apply`, which changes who can reach the app. It's the student's call, and the plan should be read first.
- **Start the notebook.** Only exploration needs it, and it costs money while it runs.
- **Move a model alias.** Only `promote.yaml` moves `champion`, with approval (contracts/mlflow.yaml).
- **Deploy an image.** Restore brings back what ran; shipping a version is CD's job.

The trigger is a person running it. Nothing runs it on a schedule or at boot.

## Rejected alternative

A boot hook on the hosts, a systemd unit on the app host, that calls serving's `/reload champion` once MLflow answers after a reboot.

## Why

- The parts that come back by themselves already do. In the S4 restore, every container was back within a minute of the hosts booting, and serving had the champion 53 s after boot, with no reload. A boot hook would duplicate serving's own retry loop.
- What doesn't come back by itself is outside the hosts: RDS and Redshift stay stopped until someone calls the AWS API, and a host can't do that with LabRole safely on every boot. A paused Redshift must stay paused when that was the point.
- The slow part of a restore is waiting in the right order (RDS takes ~7 min, Redshift ~3 min). A script waits without someone watching a console, and its timings make the runbook honest.
- The four "never" steps change who can get in, what it costs, or what riders get. Each needs a person who knows why. The script makes each one a one-line copy-paste, not a guess.
- The cost I accept: someone still has to remember to run it after Start Lab. Until they do, riders get the 12.25 fallback whenever RDS was stopped.

## What would show this was wrong

- A restore where the script reported OK, but riders didn't get the champion: then its checks are too thin.
- The same manual step, such as the IP change, needed every morning: then it's toil worth automating, with a guard.
- Running it by hand gets forgotten, so the lab stays down until someone notices: then a scheduled trigger belongs in front of it.
