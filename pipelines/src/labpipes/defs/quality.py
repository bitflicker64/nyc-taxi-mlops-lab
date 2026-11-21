"""S1: data-quality checks on stg_trips (nulls, ranges, duplicates, row counts).

Decision 006 ("Balanced"): which checks block and every threshold, set from what 2024-01..04 look like
in staging.stg_trips (measured 2026-11-21, TLC yellow trips):

  rows per month           2,964,624 .. 3,582,628
  duration <= 0            0.027% .. 0.031%
  duration > 180 min       0.061% .. 0.067%
  passenger_count null     4.73% .. 11.90%, rising every month
  zone outside 1..263      0.96% .. 1.06%
  pickup outside the month 0.0003% .. 0.0006%
  duplicate trips          0.000%

A blocking check that fails stops the run before zone_hour_stats, so the month never reaches the mart.
A warning shows in Dagster and lets the month through.
"""

from dataclasses import dataclass

import dagster as dg

from ..partitions import check_month, trip_months
from ..resources import RedshiftData
from .staging import stg_trips


@dataclass(frozen=True)
class RateRule:
    name: str
    metric: str  # a column of the rates query below
    max_pct: float  # fails above this share of the month's rows, in percent
    blocking: bool
    what: str


# Normal values are in the module docstring; each limit sits well above them.
RATE_RULES = [
    RateRule("nonpositive_duration", "nonpositive_duration", 1.0, True, "trips ending at or before they start"),
    RateRule("unknown_zone", "unknown_zone", 5.0, True, "pickup or dropoff zone outside 1..263"),
    RateRule("over_3h", "over_3h", 0.5, False, "trips longer than 180 minutes"),
    RateRule("null_passengers", "null_passengers", 25.0, False, "passenger_count missing (not a model input)"),
    RateRule("wrong_month", "wrong_month", 0.1, False, "pickup time outside the file's month"),
]
MIN_ROWS = 1_000_000  # blocking: real months have about 3 million
MAX_DUPLICATE_PCT = 0.1  # blocking: real months have none


def rates(redshift: RedshiftData, month: str) -> dict:
    """Row count and the count of rows breaking each rule, for one month."""
    m = check_month(month)
    return redshift.query(
        "select count(*) as rows, "
        "sum(case when duration_min <= 0 then 1 else 0 end) as nonpositive_duration, "
        "sum(case when duration_min > 180 then 1 else 0 end) as over_3h, "
        "sum(case when passenger_count is null then 1 else 0 end) as null_passengers, "
        "sum(case when pickup_zone_id not between 1 and 263 or dropoff_zone_id not between 1 and 263 "
        "then 1 else 0 end) as unknown_zone, "
        f"sum(case when to_char(pickup_at, 'YYYY-MM') <> '{m}' then 1 else 0 end) as wrong_month "
        f"from staging.stg_trips where trip_month = '{m}'"
    )[0]


def _pct(part, whole) -> float:
    return 100.0 * int(part or 0) / max(int(whole or 0), 1)


def _rate_check(rule: RateRule):
    @dg.asset_check(
        asset=stg_trips,
        name=rule.name,
        partitions_def=trip_months,
        blocking=rule.blocking,
        description=f"{'Blocks' if rule.blocking else 'Warns'} above {rule.max_pct}% {rule.what}.",
    )
    def _check(context: dg.AssetCheckExecutionContext, redshift: RedshiftData) -> dg.AssetCheckResult:
        r = rates(redshift, context.partition_key)
        bad, rows = int(r.get(rule.metric) or 0), int(r["rows"] or 0)
        pct = _pct(bad, rows)
        return dg.AssetCheckResult(
            passed=pct <= rule.max_pct,
            severity=dg.AssetCheckSeverity.ERROR if rule.blocking else dg.AssetCheckSeverity.WARN,
            description=f"{pct:.4f}% {rule.what} (limit {rule.max_pct}%)",
            metadata={"pct": dg.MetadataValue.float(pct), "rows": dg.MetadataValue.int(bad), "limit_pct": rule.max_pct},
        )

    return _check


rate_checks = [_rate_check(rule) for rule in RATE_RULES]


@dg.asset_check(
    asset=stg_trips,
    partitions_def=trip_months,
    blocking=True,
    description=f"Blocks a month with fewer than {MIN_ROWS:,} rows (a truncated or wrong file).",
)
def row_count(context: dg.AssetCheckExecutionContext, redshift: RedshiftData) -> dg.AssetCheckResult:
    m = check_month(context.partition_key)
    found = redshift.query(
        f"select trip_month, count(*) as n from staging.stg_trips where trip_month = '{m}' group by trip_month"
    )
    rows = int(found[0]["n"]) if found else 0
    return dg.AssetCheckResult(
        passed=rows >= MIN_ROWS,
        severity=dg.AssetCheckSeverity.ERROR,
        description=f"{rows:,} rows (minimum {MIN_ROWS:,})",
        metadata={"rows": dg.MetadataValue.int(rows), "min_rows": MIN_ROWS},
    )


@dg.asset_check(
    asset=stg_trips,
    partitions_def=trip_months,
    blocking=True,
    description=f"Blocks above {MAX_DUPLICATE_PCT}% duplicate trips (same vendor, times, zones and total).",
)
def duplicates(context: dg.AssetCheckExecutionContext, redshift: RedshiftData) -> dg.AssetCheckResult:
    m = check_month(context.partition_key)
    r = redshift.query(
        "select sum(n) as rows, sum(n - 1) as extra from ("
        "select count(*) as n from staging.stg_trips "
        f"where trip_month = '{m}' "
        "group by vendor_id, pickup_at, dropoff_at, pickup_zone_id, dropoff_zone_id, total_amount)"
    )[0]
    pct = _pct(r["extra"], r["rows"])
    return dg.AssetCheckResult(
        passed=pct <= MAX_DUPLICATE_PCT,
        severity=dg.AssetCheckSeverity.ERROR,
        description=f"{pct:.4f}% duplicate rows (limit {MAX_DUPLICATE_PCT}%)",
        metadata={"pct": dg.MetadataValue.float(pct), "extra_rows": dg.MetadataValue.int(int(r["extra"] or 0))},
    )
