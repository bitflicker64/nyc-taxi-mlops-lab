# 001: Runner credentials: the instance profile

Stage: S0 · Date: 2026-11-21 · Author: Himanshu Verma · Drafted by agent: yes

## Choice

The self-hosted runner on the app host (`lab-app`) gets AWS access only from the host's `LabInstanceProfile`, through IMDSv2. No AWS keys are stored in GitHub secrets or on the host. CD reaches GHCR with the run's own `GITHUB_TOKEN`.

## Rejected alternative

Static Learner Lab session credentials in GitHub secrets (`scripts/gh-secrets.sh`), read by the CD job.

## Why

- Session credentials die when the Learner Lab session ends. CD would fail after every Start Lab until someone re-ran `gh-secrets.sh`. Instance-profile credentials are rotated by AWS and are there again as soon as the host boots.
- Secrets in GitHub put a copy of the account's credentials somewhere else. Any workflow that can read them, including a badly written future one, can leak them. With the instance profile, only code running on the host can get credentials, and only while it runs.
- The cost I accept: every job on this runner gets LabRole, so the runner must never run untrusted code. That is held by three things together: fork-PR workflows need my approval, `cd.yaml` runs only for a successful `push` or manual run of `main` in this repo, and `make check` rejects any self-hosted job on `pull_request`.

## What would show this was wrong

- A job needs AWS from a GitHub-hosted runner (the S10 destroy workflow does). Then that one workflow needs session secrets, but this runner still shouldn't.
- Anything other than my own `main` runs on `lab-app`, visible in the runner's job history.
