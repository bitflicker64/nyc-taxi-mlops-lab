"""S2: the feature_contract check on trip_features (blocking, contracts/mlflow.yaml).

"Matches the contract" (contracts/features.yaml) means all three:

1. Columns: staging.trip_features has exactly the contract's keys, inputs and label, with the contract's
   types. A missing column breaks training; an extra one is how a leaky column would sneak in.
2. No forbidden column: nothing known only after the trip (dropoff_at, trip_distance, fare_amount, ...).
   Covered by 1 as well, but named on its own so a failure says plainly what happened.
3. Ranges: no row of the month outside the contract's ranges (zones 1..263, hour 0..23, day 0..6,
   duration in (0, 180]). trip_features.sql filters these, so any such row means the SQL or the contract
   moved, which is exactly when training must not start.
"""

import dagster as dg

from ..contracts import features_contract
from ..partitions import check_month, trip_months
from ..resources import RedshiftData
from .features import trip_features

# features.yaml type -> what Redshift reports in svv_columns.data_type.
REDSHIFT_TYPE = {
    "integer": "integer",
    "smallint": "smallint",
    "double precision": "double precision",
}


def expected_columns(contract: dict) -> dict[str, str]:
    """Column name -> Redshift data_type for every column the contract puts in the table."""
    out = {}
    for col in [*contract["keys"], *contract["inputs"], contract["label"]]:
        t = col["type"]
        out[col["name"]] = "character" if t.startswith("char(") else REDSHIFT_TYPE[t]
    return out


def column_problems(contract: dict, actual: dict[str, str]) -> list[str]:
    """Every way the table's columns (name -> data_type) differ from the contract; empty when they match."""
    expected = expected_columns(contract)
    problems = [f"forbidden column {c} (known only after the trip)" for c in contract["forbidden"] if c in actual]
    problems += [f"missing column {c}" for c in expected if c not in actual]
    problems += [f"column {c} is not in the contract" for c in actual if c not in expected and c not in contract["forbidden"]]
    problems += [f"column {c} is {actual[c]}, contract has {t}" for c, t in expected.items() if c in actual and actual[c] != t]
    return problems


def range_conditions(contract: dict) -> dict[str, str]:
    """Column -> SQL condition that is true for an out-of-range value."""
    conds = {}
    for col in [*contract["inputs"], contract["label"]]:
        lo, hi = col.get("range", [None, None])
        if lo is None:
            continue
        low = f"{col['name']} <= {lo}" if col.get("exclusive_min") else f"{col['name']} < {lo}"
        conds[col["name"]] = f"({low} or {col['name']} > {hi} or {col['name']} is null)"
    return conds


@dg.asset_check(
    asset=trip_features,
    partitions_def=trip_months,
    blocking=True,
    description="Columns, types and ranges are exactly contracts/features.yaml; no post-trip column.",
)
def feature_contract(context: dg.AssetCheckExecutionContext, redshift: RedshiftData) -> dg.AssetCheckResult:
    month = check_month(context.partition_key)
    contract = features_contract()
    schema, table = contract["table"].split(".")
    actual = {
        r["column_name"]: r["data_type"]
        for r in redshift.query(
            "select column_name, data_type from svv_columns "
            f"where table_schema = '{schema}' and table_name = '{table}'"
        )
    }
    problems = column_problems(contract, actual)

    conds = range_conditions(contract)
    counts = redshift.query(
        "select " + ", ".join(f"sum(case when {c} then 1 else 0 end) as {n}" for n, c in conds.items())
        + f" from {contract['table']} where trip_month = '{month}'"
    )
    out_of_range = {n: int((counts[0] if counts else {}).get(n) or 0) for n in conds}
    problems += [f"{n}: {k} row(s) out of range" for n, k in out_of_range.items() if k]

    return dg.AssetCheckResult(
        passed=not problems,
        severity=dg.AssetCheckSeverity.ERROR,
        description="matches the contract" if not problems else f"{len(problems)} problem(s): {problems[0]}",
        metadata={
            "problems": dg.MetadataValue.json(problems),
            "columns": dg.MetadataValue.json(actual),
            "out_of_range": dg.MetadataValue.json(out_of_range),
        },
    )
