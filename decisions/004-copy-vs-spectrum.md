# 004: COPY each month into Redshift, not Spectrum

Stage: S1 · Date: 2026-11-21 · Author: Himanshu Verma · Drafted by agent: yes

## Choice

`raw_trips` COPYs each month's Parquet file into `ingest.raw_trips`, replacing that month's rows in one transaction. Staging, quality checks and the mart all read the loaded table, not the files.

## Rejected alternative

Redshift Spectrum: an external table over `trips/` in the data bucket, queried in place, with nothing copied.

## Why

- The quality checks must judge the data the mart is built from. With Spectrum, the files could change after the checks pass, and the next query would read the new contents without any gate. With COPY, what passed the checks is what's in the table until the next load.
- `stg_trips` and the mart query the trips many times per run. With Spectrum each query rescans S3 (slower, and billed per TB scanned). Loaded data is read from the cluster's own storage, sorted by pickup time.
- The cost I accept: the data is stored twice (S3 and Redshift), and the load has to be made safe to re-run (delete plus insert in one transaction, keyed by `source_file`).

## What would show this was wrong

- Months become too big or too many for one `ra3.large` node's storage, or loads take longer than the gap between new files.
- We need to query files that are never meant to reach the mart (ad-hoc exploration of raw data). Then a Spectrum table next to the COPY path would be cheaper than loading them.
