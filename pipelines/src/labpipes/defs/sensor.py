"""S1: the S3 sensor and the job it starts.

Every minute, list trips/ in the data bucket. A month seen for the first time becomes a new trip_months
partition; a new or changed file (its ETag, S3's content fingerprint, is different) starts one ingest_month
run for that month, from the zone lookup and trip_files through zone_hour_stats.

The cursor (Dagster's memory between sensor ticks) is {month: etag} for every file already requested. The
run_key "<month>:<etag>" makes a repeated request a no-op, so a lost cursor never loads a file twice.
"""

import json

import dagster as dg

from ..partitions import trip_months
from ..resources import DataBucket

# One month, end to end. The blocking checks (schema_contract, the blocking quality checks,
# feature_contract) are part of the run, so a month that fails one never reaches what's after it.
# zone_hour_stats is unpartitioned and rebuilt in full; taxi_zones is small and reloaded so the mart
# always has zone names. trip_features (S2) is the month's model inputs, ready for train_model.
ingest_month = dg.define_asset_job(
    "ingest_month",
    selection=dg.AssetSelection.assets(
        "taxi_zones", "trip_files", "raw_trips", "stg_trips", "zone_hour_stats", "trip_features"
    ),
)


@dg.sensor(job=ingest_month, minimum_interval_seconds=60)
def trip_files_sensor(context: dg.SensorEvaluationContext, data_bucket: DataBucket):
    seen: dict[str, str] = json.loads(context.cursor or "{}")
    files = {f.month: f.etag for f in data_bucket.trip_files()}
    changed = sorted(m for m, etag in files.items() if seen.get(m) != etag)
    if not changed:
        return dg.SkipReason(f"no new or changed files ({len(files)} known)")

    new_months = [m for m in changed if m not in seen]
    return dg.SensorResult(
        run_requests=[dg.RunRequest(partition_key=m, run_key=f"{m}:{files[m]}") for m in changed],
        dynamic_partitions_requests=[trip_months.build_add_request(new_months)] if new_months else [],
        cursor=json.dumps({**seen, **{m: files[m] for m in changed}}, sort_keys=True),
    )
