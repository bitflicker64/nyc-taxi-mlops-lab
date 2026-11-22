# 010: Serving picks up a new champion by reloading in place

Stage: S3 · Date: 2026-11-22 · Author: Himanshu Verma · Drafted by agent: yes

## Choice

`promote.yaml` moves the `champion` alias, then calls `deploy/web.sh reload champion`. The `serving-champion` container loads the version the alias now points at and warms it up while the old model keeps answering; only then does it switch. If the check after promotion fails (riders' `/predict` doesn't report the new champion), the workflow puts the old alias back and reloads again.

## Rejected alternative

Restart `serving-champion`, which loads whatever `@champion` points at on start-up.

## Why

- A restart leaves a window with no model. During it the app falls back to the static 12.25-minute median for every rider: correct, but a visible drop in quality on every promotion and every model rollback.
- Reloading in place has no window: the old model serves until the new one has answered a warm-up prediction. A promotion or rollback is then invisible to riders unless the new model itself is worse.
- The cost I accept: for a few seconds serving holds two models in memory, and the swap is a call that can fail on its own (MLflow unreachable, bad artifact). The workflow checks the result through the app and undoes the alias move when it fails.

## What would show this was wrong

- Serving runs out of memory during a reload as models grow (two copies at once).
- A reload reports success but riders still get the old version, meaning the in-place swap has a bug a restart wouldn't have.
