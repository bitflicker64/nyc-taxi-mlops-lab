"""S2: training_set, trained_model, eval_report, the beats_champion gate and the challenger asset.

The train_model job runs them in that order. Decisions (records in decisions/):

- 007: the notebook explores; everything that can produce a served model lives here.
- 008: the promotion gate. A new version must have an MAE at least MARGIN lower than what it's compared
  with (the champion, or the v1 baseline before there is one), a p95 single-row latency of at most
  MAX_P95_MS, and no slice (airport, Manhattan, outer boroughs, night) more than MAX_SLICE_WORSE worse.
- 009: the training window. The latest month in trip_features is the holdout; the TRAIN_MONTHS months
  before it train. With 2024-01..04 that is train 2024-01..03, test 2024-04.

The pipeline only ever moves `challenger`. `champion` belongs to S3's approval-gated workflow.
"""

import os
from pathlib import Path

import dagster as dg

from ..contracts import features_contract, mlflow_contract
from ..partitions import check_month
from ..resources import ArtifactBucket, MlflowTracking, RedshiftData

TRAIN_MONTHS = 3
MARGIN = 0.02  # MAE must be at least 2% lower
MAX_P95_MS = 50.0  # single-row p95 latency; the app gives up on serving after 300 ms
MAX_SLICE_WORSE = 0.10  # no slice's MAE more than 10% higher
SLICES = ["airport", "manhattan", "outer", "night"]
INPUTS = "pickup_zone_id, dropoff_zone_id, pickup_hour, pickup_dow, duration_min"


class WindowConfig(dg.Config):
    holdout_month: str = ""  # empty: the latest month in staging.trip_features
    train_months: int = TRAIN_MONTHS


def pick_window(months: list[str], holdout: str, train_months: int) -> tuple[list[str], str]:
    """(training months, holdout month) from the months that have trip_features."""
    holdout = check_month(holdout or months[-1])
    window = [m for m in months if m < holdout][-train_months:]
    if holdout not in months or len(window) < train_months:
        raise dg.Failure(f"need {train_months} months before {holdout} plus {holdout} itself; trip_features has {months}")
    return window, holdout


def _in(months: list[str]) -> str:
    """A SQL IN list for months, quoted for use inside UNLOAD's single-quoted query."""
    return ", ".join(f"''{check_month(m)}''" for m in months)


@dg.asset(group_name="model", kinds={"redshift", "s3"}, pool="redshift")
def training_set(context: dg.AssetExecutionContext, config: WindowConfig, redshift: RedshiftData,
                 artifacts: ArtifactBucket) -> dict:
    """UNLOAD the window, the holdout and the window's zone-pair distances to the artifact bucket."""
    months = [r["trip_month"] for r in redshift.query("select distinct trip_month from staging.trip_features order by 1")]
    window, holdout = pick_window(months, config.holdout_month, config.train_months)
    prefix = f"training/{window[0]}..{window[-1]}_{holdout}"
    unload = "to '{uri}' iam_role default format as parquet allowoverwrite"
    redshift.execute(
        f"unload ('select {INPUTS} from staging.trip_features where trip_month in ({_in(window)})') "
        + unload.format(uri=artifacts.uri(f"{prefix}/train/")),
        f"unload ('select {INPUTS} from staging.trip_features where trip_month in ({_in([holdout])})') "
        + unload.format(uri=artifacts.uri(f"{prefix}/holdout/")),
        # est_distance_miles (features.yaml, derived): the window's median distance per zone pair. From
        # stg_trips, because trip_distance is post-trip and never a column of trip_features.
        "unload ('select pickup_zone_id, dropoff_zone_id, median(trip_distance) as est_distance_miles "
        f"from staging.stg_trips where trip_month in ({_in(window)}) and duration_min > 0 and duration_min <= 180 "
        "and trip_distance > 0 and pickup_zone_id between 1 and 263 and dropoff_zone_id between 1 and 263 "
        "group by 1, 2') " + unload.format(uri=artifacts.uri(f"{prefix}/distances/")),
    )
    window_sql = ", ".join(f"'{m}'" for m in window)
    counts = redshift.query(
        f"select sum(case when trip_month in ({window_sql}) then 1 else 0 end) as train, "
        f"sum(case when trip_month = '{holdout}' then 1 else 0 end) as holdout from staging.trip_features"
    )[0]
    context.add_output_metadata({
        "training_window": f"{window[0]}..{window[-1]}",
        "holdout_month": holdout,
        "train_rows": dg.MetadataValue.int(int(counts["train"])),
        "holdout_rows": dg.MetadataValue.int(int(counts["holdout"])),
        "uri": artifacts.uri(prefix),
    })
    return {"window": window, "holdout": holdout, "prefix": prefix}


@dg.asset(group_name="model", kinds={"lightgbm", "mlflow"})
def trained_model(context: dg.AssetExecutionContext, training_set: dict, artifacts: ArtifactBucket,
                  tracking: MlflowTracking) -> dict:
    """Fit v2 (LightGBM) on the window, log it to MLflow as a pyfunc and register a version."""
    import mlflow

    import tripmodel

    contract = mlflow_contract()
    name = contract["registered_model"]
    prefix, window = training_set["prefix"], training_set["window"]
    train = artifacts.read_parquet(f"{prefix}/train/")
    distances = artifacts.read_parquet(f"{prefix}/distances/")
    client = tracking.client()

    model = tripmodel.LightGBMModel().fit(train, distances)
    with mlflow.start_run(run_name=f"v2 {window[0]}..{window[-1]}") as run:
        mlflow.log_params({"model": "v2 lightgbm", "training_window": f"{window[0]}..{window[-1]}",
                           "holdout_month": training_set["holdout"], "train_rows": len(train), **model.params})
        info = mlflow.pyfunc.log_model(
            name="model",
            python_model=model,
            signature=tripmodel.model_signature(),
            code_paths=[str(Path(tripmodel.__file__).parent)],
            registered_model_name=name,
        )
    version = str(info.registered_model_version)
    tags = {
        "training_window": f"{window[0]}..{window[-1]}",
        "holdout_month": training_set["holdout"],
        "features_version": str(features_contract()["version"]),
        "git_sha": os.environ.get("GIT_SHA", "unknown"),
    }
    for k, v in tags.items():
        client.set_model_version_tag(name, version, k, v)
    context.add_output_metadata({"version": version, "run_id": run.info.run_id, **tags})
    return {"version": version, "run_id": run.info.run_id}


@dg.asset(group_name="model", kinds={"mlflow"})
def eval_report(context: dg.AssetExecutionContext, training_set: dict, trained_model: dict, artifacts: ArtifactBucket,
                tracking: MlflowTracking, redshift: RedshiftData) -> dict:
    """The new version and the champion (or the v1 baseline) on the same holdout month."""
    from mlflow.exceptions import MlflowException

    import tripmodel

    name = mlflow_contract()["registered_model"]
    version, run_id = trained_model["version"], trained_model["run_id"]
    prefix = training_set["prefix"]
    holdout = artifacts.read_parquet(f"{prefix}/holdout/")
    boroughs = {int(r["location_id"]): r["borough"] for r in redshift.query("select location_id, borough from ingest.taxi_zones")}
    client = tracking.client()

    new = evaluate_uri(f"models:/{name}/{version}", holdout, boroughs)
    try:
        champion = client.get_model_version_by_alias(name, "champion")
        reference = evaluate_uri(f"models:/{name}@champion", holdout, boroughs)
        against = f"champion v{champion.version}"
    except MlflowException:  # no champion yet: the v1 baseline, fit on the same window
        baseline = tripmodel.BaselineModel().fit(artifacts.read_parquet(f"{prefix}/train/"),
                                                 artifacts.read_parquet(f"{prefix}/distances/"))
        reference = tripmodel.evaluate(lambda df: baseline.predict(None, df), holdout, boroughs)
        against = "v1 baseline"

    for k, v in new.items():
        client.log_metric(run_id, k, v)
    client.log_metric(run_id, "champion_mae_min", reference["mae_min"])
    for k, v in {"mae_min": new["mae_min"], "p95_latency_ms": new["p95_latency_ms"],
                 "champion_mae_min": reference["mae_min"]}.items():
        client.set_model_version_tag(name, version, k, f"{v:.4f}")

    report = {"version": version, "against": against, "new": new, "reference": reference}
    context.add_output_metadata({
        "against": against,
        "mae_min": dg.MetadataValue.float(new["mae_min"]),
        "reference_mae_min": dg.MetadataValue.float(reference["mae_min"]),
        "p95_latency_ms": dg.MetadataValue.float(new["p95_latency_ms"]),
        "gate": dg.MetadataValue.json(gate_failures(report) or ["passes"]),
    })
    return report


def evaluate_uri(uri: str, holdout, boroughs: dict) -> dict:
    """Evaluate a registered version the way serving loads it (a pyfunc from the registry)."""
    import mlflow

    import tripmodel

    return tripmodel.evaluate(mlflow.pyfunc.load_model(uri).predict, holdout, boroughs)


def gate_failures(report: dict) -> list[str]:
    """Every rule of decision 008 the new version breaks; empty means it may become challenger."""
    new, ref = report["new"], report["reference"]
    failures = []
    if new["mae_min"] > ref["mae_min"] * (1 - MARGIN):
        failures.append(f"MAE {new['mae_min']:.3f} is not {MARGIN:.0%} below {report['against']}'s {ref['mae_min']:.3f}")
    if new["p95_latency_ms"] > MAX_P95_MS:
        failures.append(f"p95 latency {new['p95_latency_ms']:.1f} ms is over {MAX_P95_MS:.0f} ms")
    for s in SLICES:
        k = f"mae_min.{s}"
        if k in new and k in ref and new[k] > ref[k] * (1 + MAX_SLICE_WORSE):
            failures.append(f"{s} MAE {new[k]:.3f} is more than {MAX_SLICE_WORSE:.0%} worse than {ref[k]:.3f}")
    return failures


@dg.asset_check(asset=eval_report, blocking=True,
                description=f"MAE {MARGIN:.0%} below the champion (or v1), p95 <= {MAX_P95_MS:.0f} ms, "
                            f"no slice {MAX_SLICE_WORSE:.0%} worse.")
def beats_champion(eval_report: dict) -> dg.AssetCheckResult:
    failures = gate_failures(eval_report)
    new, ref = eval_report["new"], eval_report["reference"]
    return dg.AssetCheckResult(
        passed=not failures,
        severity=dg.AssetCheckSeverity.ERROR,
        description=f"v{eval_report['version']} vs {eval_report['against']}: " + ("; ".join(failures) or "passes"),
        metadata={"version": eval_report["version"], "against": eval_report["against"],
                  "mae_min": dg.MetadataValue.float(new["mae_min"]), "reference_mae_min": dg.MetadataValue.float(ref["mae_min"]),
                  "p95_latency_ms": dg.MetadataValue.float(new["p95_latency_ms"]), "failures": dg.MetadataValue.json(failures)},
    )


@dg.asset(group_name="model", kinds={"mlflow"})
def challenger(context: dg.AssetExecutionContext, eval_report: dict, tracking: MlflowTracking) -> dg.MaterializeResult:
    """Point the `challenger` alias at the version that passed beats_champion (blocking: this asset
    never runs when the gate fails). Never touches `champion`."""
    name = mlflow_contract()["registered_model"]
    tracking.client().set_registered_model_alias(name, "challenger", eval_report["version"])
    return dg.MaterializeResult(metadata={"version": eval_report["version"], "against": eval_report["against"]})


train_model = dg.define_asset_job(
    "train_model",
    selection=dg.AssetSelection.assets(training_set, trained_model, eval_report, challenger),
)
