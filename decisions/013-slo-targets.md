# 013: Three SLOs on /predict, each 99% over 30 days, paged on a fast burn

Stage: S5 · Date: 2026-11-22 · Author: Himanshu Verma · Drafted by agent: yes

## Choice

Three SLIs on `/predict`, from contracts/metrics.yaml (details in `observability/slo.yaml`):

- **Model answered:** quotes from the model, not the 12.25 fallback (`lab_predictions_total{source}`).
- **Availability:** requests not answered with a 5xx (`lab_http_requests_total`). A 422 counts as good.
- **Latency:** requests answered in 250 ms or less, inside the app (`lab_http_request_duration_seconds`).

Each has an objective of **99% over 30 days**. Alerts use multi-window burn rates
(`observability/rules/slo.rules.yaml`):

- a page at a burn over 14.4x on both the 1h and the 5m windows;
- a ticket at a burn over 6x on both the 6h and the 30m windows.

promtool tests (`slo.rules.test.yaml`) prove five things:

- an outage pages, for each SLO;
- a two-minute spike doesn't page;
- a page clears within minutes of the fix;
- a flood of 422s never pages;
- `/healthz` traffic doesn't dilute the ratio.

## Rejected alternative

Availability alone, at 99.9%, paged whenever the 5xx rate is above zero for a few minutes.

## Why

- **Availability alone misses the failure that matters most.** When serving is down, the app answers 200 with 12.25. Riders get a non-answer, and availability stays at 100%. Only "model answered" sees it.
- **99%, not 99.9%:** this is one small host with one live colour, and S4 measured a reboot at about a minute. 99.9% is 43 minutes a month, and ordinary operations would spend most of it. A target the setup can't keep teaches everyone to ignore the alert.
- **Burn rate, not "any error":** a page means the budget is really going (14.4x over 1h is 2% of the month gone in an hour) and is still going (the 5m window). A two-minute serving reload doesn't wake anyone, and the page clears soon after the fix.
- **250 ms:** it's a bucket boundary, so it can be measured. It's also under serving's 300 ms timeout, so a request that would fall back already counts as slow.
- **The cost I accept:** a complete outage takes about 9 minutes to page, while the 1h ratio passes 14.4%. A faster page would also fire on blips.

## What would show this was wrong

- Riders notice an outage before the page does, often: then the page is too slow, and a shorter long window (30m) or an extra page tier belongs here.
- The page fires and nobody needed to act: then the threshold or the SLI is wrong.
- The budget is never close to spent for weeks: then 99% is too loose for what the system really does, and 99.5% is the honest number.
