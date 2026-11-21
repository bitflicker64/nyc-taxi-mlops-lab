"""S1: the schema_contract check on trip_files.

Compares the landed file's Parquet footer (the DataBucket resource reads it with ranged GETs, so no
rows are downloaded) with contracts/trips.schema.yaml, before raw_trips copies anything.
contracts/platform.yaml says this check is blocking: when it fails, raw_trips and everything after it
are skipped for that month.

What counts as a mismatch follows from how Parquet COPY works: it maps columns by position and fails on
a column-count mismatch. So the file must have exactly the contract's columns, in the contract's order,
with the contract's storage types. Names compare case-insensitively (the contract says so: TLC renamed
airport_fee to Airport_fee, and positional COPY never sees names).
"""

import dagster as dg

from ..contracts import Column, trips_schema
from ..partitions import trip_months
from ..resources import DataBucket
from .trip_files import trip_files


def schema_differences(expected: list[Column], actual: list[Column]) -> list[str]:
    """Every way `actual` differs from `expected`, as readable lines; empty when they match."""
    problems = []
    if len(actual) != len(expected):
        problems.append(f"{len(actual)} columns, contract has {len(expected)}")
    # Pairs up the overlapping positions; extra or missing columns are reported below.
    for i, (want, got) in enumerate(zip(expected, actual, strict=False), start=1):
        if got.name.casefold() != want.name.casefold():
            problems.append(f"column {i} is {got.name}, contract has {want.name}")
        if got.type != want.type:
            problems.append(f"column {i} ({want.name}) is {got.type}, contract has {want.type}")
    for i, extra in enumerate(actual[len(expected) :], start=len(expected) + 1):
        problems.append(f"column {i} ({extra.name} {extra.type}) is not in the contract")
    for i, missing in enumerate(expected[len(actual) :], start=len(actual) + 1):
        problems.append(f"column {i} ({missing.name} {missing.type}) is missing")
    return problems


@dg.asset_check(
    asset=trip_files,
    partitions_def=trip_months,
    blocking=True,
    description="The file's Parquet schema is exactly contracts/trips.schema.yaml (count, order, names, types).",
)
def schema_contract(context: dg.AssetCheckExecutionContext, data_bucket: DataBucket) -> dg.AssetCheckResult:
    month = context.partition_key
    actual = data_bucket.footer(month).columns
    problems = schema_differences(trips_schema(), actual)
    return dg.AssetCheckResult(
        passed=not problems,
        severity=dg.AssetCheckSeverity.ERROR,
        description="matches the contract" if not problems else f"{len(problems)} difference(s): {problems[0]}",
        metadata={
            "uri": data_bucket.trip_uri(month),
            "differences": dg.MetadataValue.json(problems),
            "file_columns": dg.MetadataValue.json([f"{c.name} {c.type}" for c in actual]),
        },
    )
