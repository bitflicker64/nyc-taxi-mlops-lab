# 002: Image tag scheme: sha-<7>, no moving tags

Stage: S0 · Date: 2026-11-21 · Author: Himanshu Verma · Drafted by agent: yes

## Choice

Each image is tagged `sha-<first 7 characters of the commit>`, for example `ghcr.io/bitflicker64/nyc-taxi-mlops-lab/app:sha-4a0668f`. CI pushes one tag per `main` commit and nothing else. A deploy and a rollback both name an exact `sha-` tag. Rolling back means redeploying the earlier commit's tag.

## Rejected alternative

`sha-<7>` plus a moving `latest` tag that CD deploys.

## Why

- A tag that names a commit answers "what is running?" without anything else: `/version`'s `git_sha` and the image tag point at the same commit, and CD's smoke test checks that they match.
- `latest` changes under you. Two hosts, or one host before and after a pull, can run different builds under the same name, and a rollback to "latest" is not a rollback. It also hides which commit failed when a deploy breaks.
- Seven characters match the given platform and serving workflows and are short enough to type during an incident. A clash between two commits on 7 characters is very unlikely in a repo this size.

## What would show this was wrong

- Two commits share a 7-character prefix (GitHub would start showing longer short SHAs).
- A rollback needs a build that no longer exists because GHCR retention removed old tags.
- I need to rebuild the same commit with a different base image. Then the same tag would point at two different images, and I would need a digest or a build suffix.
