# 009: Training window: the three months before the latest; the latest is the holdout

Stage: S2 · Date: 2026-11-21 · Author: Himanshu Verma · Drafted by agent: yes

## Choice

`training_set` takes the latest month in `staging.trip_features` as the holdout and the three months before it as the training window. With 2024-01..04 loaded: train on 2024-01..03, evaluate on 2024-04. The zone-pair distance lookup is built from the window only. The window rolls forward as months land.

## Rejected alternative

Train on 2024-02..03 only and drop January (holidays and winter weather that may not repeat).

## Why

- Testing on the month after the window is how the model will be used: trained on the past, asked about the future. A random split of the same months would let the model see the test month's patterns and look better than it is.
- Three months give rare zone pairs (outer boroughs, airports at night) enough trips to learn from. Two months would leave more of them on the fallbacks.
- January's oddities are a reason to watch drift, not to throw data away: the drift checks (S8) compare each new month with the champion's window.
- Three months is also what `drift.py` assumes when there is no champion yet, so both use the same reference.

## What would show this was wrong

- Drift between the window and new months stays high (PSI above 0.2) even though the model is recent: then a shorter or weighted window fits better.
- A model trained on fewer, more recent months beats this one on the next holdout.
