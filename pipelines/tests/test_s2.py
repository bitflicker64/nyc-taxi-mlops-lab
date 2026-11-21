"""S2: feature_contract, the training window, the promotion gate, and train_model end to end against
fakes (Redshift UNLOADs synthetic trips into a local "bucket"; MLflow is a local SQLite store)."""

import dagster as dg
import fakes
import mlflow
import pytest
from fakes import FakeRedshift, LocalArtifacts, unloading_redshift

from labpipes.contracts import features_contract, mlflow_contract
from labpipes.defs.feature_check import column_problems, expected_columns, feature_contract, range_conditions
from labpipes.defs.features import trip_features
from labpipes.defs.training import (
    beats_champion,
    challenger,
    eval_report,
    gate_failures,
    pick_window,
    trained_model,
    training_set,
)
from labpipes.partitions import trip_months
from labpipes.resources import MlflowTracking

# --- feature_contract ---------------------------------------------------------------------------


def test_the_contract_table_has_no_problems():
    c = features_contract()
    assert column_problems(c, expected_columns(c)) == []


def test_a_post_trip_column_is_named_as_forbidden():
    c = features_contract()
    problems = column_problems(c, {**expected_columns(c), "trip_distance": "double precision"})
    assert problems == ["forbidden column trip_distance (known only after the trip)"]


def test_missing_extra_and_retyped_columns_are_problems():
    c = features_contract()
    cols = expected_columns(c)
    cols.pop("pickup_dow")
    cols["pickup_hour"] = "integer"
    cols["weather"] = "character varying"
    problems = column_problems(c, cols)
    assert "missing column pickup_dow" in problems
    assert "column weather is not in the contract" in problems
    assert "column pickup_hour is integer, contract has smallint" in problems


def test_label_range_excludes_zero():
    conds = range_conditions(features_contract())
    assert conds["duration_min"].startswith("(duration_min <= 0 ")
    assert conds["pickup_zone_id"].startswith("(pickup_zone_id < 1 ")


class ContractRedshift(FakeRedshift):
    """svv_columns answers with `columns`; range counts answer with `bad` out-of-range durations."""

    columns: dict = {}
    bad: int = 0

    def query(self, sql: str) -> list[dict]:
        fakes.LOG.append(sql)
        if "from svv_columns" in sql:
            return [{"column_name": k, "data_type": v} for k, v in self.columns.items()]
        if "out of range" in sql or "sum(case when (" in sql:
            return [{"duration_min": self.bad}]
        return super().query(sql)


def _features_run(columns: dict, bad: int = 0):
    instance = dg.DagsterInstance.ephemeral()
    instance.add_dynamic_partitions(trip_months.name, ["2024-01"])
    rs = ContractRedshift(cluster_id="lab", database="lab", db_user="labadmin", columns=columns, bad=bad)
    result = dg.materialize([trip_features, feature_contract], partition_key="2024-01", instance=instance,
                            resources={"redshift": rs}, raise_on_error=False)
    (check,) = result.get_asset_check_evaluations()
    return check


def test_feature_contract_passes_on_the_contract_table():
    assert _features_run(expected_columns(features_contract())).passed


def test_feature_contract_blocks_a_leaky_column_and_bad_labels():
    cols = {**expected_columns(features_contract()), "fare_amount": "double precision"}
    check = _features_run(cols, bad=12)
    assert not check.passed and check.blocking
    problems = check.metadata["problems"].value
    assert any("forbidden column fare_amount" in p for p in problems)
    assert any("duration_min: 12 row(s) out of range" in p for p in problems)


# --- window and gate ----------------------------------------------------------------------------

MONTHS = ["2024-01", "2024-02", "2024-03", "2024-04"]


def test_default_window_is_the_three_months_before_the_latest():
    assert pick_window(MONTHS, "", 3) == (["2024-01", "2024-02", "2024-03"], "2024-04")


def test_too_short_a_window_fails():
    with pytest.raises(dg.Failure):
        pick_window(MONTHS, "2024-03", 3)


def _report(new_mae, ref_mae=10.0, p95=5.0, airport=(10.0, 10.0)):
    return {"version": "2", "against": "v1 baseline",
            "new": {"mae_min": new_mae, "p95_latency_ms": p95, "mae_min.airport": airport[0]},
            "reference": {"mae_min": ref_mae, "p95_latency_ms": 1.0, "mae_min.airport": airport[1]}}


def test_gate_passes_a_clear_win():
    assert gate_failures(_report(9.0)) == []


def test_gate_needs_two_percent_not_one():
    assert gate_failures(_report(9.9)) and not gate_failures(_report(9.79))


def test_gate_blocks_slow_models_and_worse_slices():
    assert any("p95" in f for f in gate_failures(_report(9.0, p95=80.0)))
    assert any("airport" in f for f in gate_failures(_report(9.0, airport=(11.5, 10.0))))


# --- train_model end to end -------------------------------------------------------------------------


def test_train_model_registers_a_tagged_version_and_sets_challenger(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_SHA", "abc1234")
    uri = f"sqlite:///{tmp_path}/mlflow.db"
    contract = mlflow_contract()
    mlflow.set_tracking_uri(uri)
    mlflow.create_experiment(contract["experiment"], artifact_location=str(tmp_path / "artifacts"))

    resources = {
        "redshift": unloading_redshift(str(tmp_path / "bucket")),
        "artifacts": LocalArtifacts(bucket="art", root=str(tmp_path / "bucket")),
        "tracking": MlflowTracking(tracking_uri=uri),
    }
    result = dg.materialize([training_set, trained_model, eval_report, beats_champion, challenger], resources=resources)
    assert result.success
    (gate,) = result.get_asset_check_evaluations()
    assert gate.passed, gate.description

    client = mlflow.MlflowClient(uri)
    mv = client.get_model_version_by_alias(contract["registered_model"], "challenger")
    assert set(contract["version_tags"]) <= set(mv.tags)
    assert mv.tags["training_window"] == "2024-01..2024-03" and mv.tags["holdout_month"] == "2024-04"
    assert mv.tags["git_sha"] == "abc1234"
    assert "champion" not in client.get_registered_model(contract["registered_model"]).aliases
