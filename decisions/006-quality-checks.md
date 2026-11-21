# 006: Data-quality checks: what blocks, what warns, and the thresholds

Stage: S1 · Date: 2026-11-21 · Author: Himanshu Verma · Drafted by agent: yes

## Choice

"Balanced": seven checks on `stg_trips`, per month. Blocking checks stop the run before `zone_hour_stats`, so the month never reaches the mart; warnings show in Dagster and let it through.

| Check | Blocks or warns | Fails above | Measured in 2024-01..04 |
| --- | --- | --- | --- |
| `row_count` | blocks | under 1,000,000 rows | 2,964,624 .. 3,582,628 |
| `duplicates` (same vendor, times, zones, total) | blocks | 0.1% | 0% |
| `nonpositive_duration` | blocks | 1% | 0.027% .. 0.031% |
| `unknown_zone` (outside 1..263) | blocks | 5% | 0.96% .. 1.06% |
| `over_3h` | warns | 0.5% | 0.061% .. 0.067% |
| `null_passengers` | warns | 25% | 4.73% .. 11.90%, rising |
| `wrong_month` (pickup outside the file's month) | warns | 0.1% | 0.0003% .. 0.0006% |

`schema_contract` on `trip_files` blocks too (contract, not a choice).

## Rejected alternative

"Strict": every check blocks, at about three times the normal rate (for example durations at or below zero over 0.1%, under 2 million rows).

## Why

- The blocking checks guard what the model learns from in S2: a truncated file, a duplicated load, broken timestamps or a broken zone mapping would all bend the medians in the mart. Their limits sit 5 to 30 times above anything a real month showed, so a real month passes and a corrupt one does not.
- The warnings cover things the mart and model can live with: long meter-left-running trips are a sliver, and `passenger_count` is not a model input. Its missing share doubled between February and March, so a hard limit near today's value would start blocking real months soon.
- Strict was rejected because a stopped pipeline has a cost too: a slightly odd but real month (a holiday, a TLC publishing hiccup) would hold every later month out of the mart until someone looked.

## What would show this was wrong

- Gameday's corrupt month reaches the mart with every check green.
- A real month gets blocked, which means a limit is too close to normal.
- `null_passengers` keeps rising toward 25%: decide then whether it matters or drop the check.
