# 011: The blue-green switch is automatic once the idle colour passes its smoke test

Stage: S3 · Date: 2026-11-22 · Author: Himanshu Verma · Drafted by agent: yes

## Choice

CD deploys the new image to the idle colour only, then runs `deploy/web.sh smoke <idle> <commit>` against it through Traefik (pinned with `X-Lab-Color`): `/version` must report the commit being deployed and `/predict` must answer. If it passes, CD switches all traffic to that colour, then checks what riders get (`/version`, `/predict`, a 422 for an invalid zone). If that last check fails, CD switches back to the colour that was live. The old colour keeps running until the next deploy; `rollback.yaml` switches to it without deploying.

## Rejected alternative

Deploy and smoke-test the idle colour automatically, then wait for a human approval (a `production`-style environment) before switching.

## Why

- Every app change already passed `make check` in CI, and the smoke test runs against the exact container that will take traffic. A human looking at the same green checks adds waiting, not information.
- Blue-green makes a bad switch cheap: the previous colour is still running, so going back is a switch measured in seconds, and CD does it on its own when riders' check fails.
- Model changes are different and do wait for approval (`promote.yaml`): a model's behaviour isn't covered by a smoke test, and the gate only judged it offline.
- The cost I accept: anything the smoke test doesn't check can reach riders, until someone notices and runs `rollback.yaml`.

## What would show this was wrong

- A deploy passes the smoke test and still breaks riders in a way a short manual look would have caught.
- Rollbacks of app versions become frequent: then the smoke test is too thin, or a canary step belongs before the full switch.
