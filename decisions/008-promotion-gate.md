# 008: Promotion gate: 2% lower MAE, p95 at most 50 ms, no slice 10% worse

Stage: S2 · Date: 2026-11-21 · Author: Himanshu Verma · Drafted by agent: yes

## Choice

A new version becomes `challenger` only if, on the same holdout month as what it's compared with (the `champion`, or the v1 baseline before there is one), all of these hold (`beats_champion`, blocking):

1. Its MAE is at least 2% lower.
2. Its p95 single-row prediction latency is at most 50 ms.
3. No slice (airport, Manhattan, outer boroughs, night) has an MAE more than 10% higher.

## Rejected alternative

Any improvement in overall MAE, however small, with only the latency limit.

## Why

- MAE (average minutes off) suits trip durations: a few three-hour trips would dominate RMSE, and riders feel each minute of error the same way.
- Two training runs on the same data differ by a fraction of a percent. Without a margin, near-identical models would swap back and forth, and every swap is a promotion and a deploy with its own risk.
- Overall MAE is mostly Manhattan trips. The slice rule stops a model winning overall while getting much worse at airport trips, which are the long, high-stakes quotes.
- The app falls back to the static median when serving takes more than 300 ms. 50 ms at p95 leaves room for the network hop, Traefik and the app.

## What would show this was wrong

- Real improvements keep failing by a hair at 2% (the margin is too strict for the noise we actually see).
- A promoted model makes riders complain about one kind of trip that none of the four slices covers.
- Serving's own p95 in S5 is far from the in-process number measured here.
