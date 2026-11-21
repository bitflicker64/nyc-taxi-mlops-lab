# 005: Trip tables: sort by pickup time, DISTSTYLE AUTO

Stage: S1 · Date: 2026-11-21 · Author: Himanshu Verma · Drafted by agent: yes

## Choice

`ingest.raw_trips` is `SORTKEY (tpep_pickup_datetime)` with `DISTSTYLE AUTO`. `staging.stg_trips` (given) is already sorted by `pickup_at`, so both trip tables are ordered by time.

## Rejected alternative

`DISTKEY (PULocationID)`, so rows with the same pickup zone sit on the same node and joins with `taxi_zones` don't move data between nodes.

## Why

- The cluster is one `ra3.large` node. A distribution key only decides which node holds a row, so with one node it changes nothing, and it would skew storage if we ever went to two nodes (a few zones like JFK and Midtown hold far more trips than others). `AUTO` lets Redshift pick, and switch to `EVEN` or `ALL` if the cluster grows.
- Almost every query filters by time: a month reload, the quality checks for one month, and later training windows. A sort key on pickup time lets those queries skip disk blocks outside the range.
- `taxi_zones` is tiny (about 265 rows). Redshift copies a table that small to every node anyway, so a zone join never needs a dist key to be fast.

## What would show this was wrong

- The cluster goes to 2 nodes and `svv_table_info` shows the trip tables skewed (`skew_rows` well above 1).
- The slowest queries filter by zone rather than by time. Then a compound sort key starting with the zone would serve them better.
