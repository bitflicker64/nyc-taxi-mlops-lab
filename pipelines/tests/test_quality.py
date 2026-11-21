"""S1: the data-quality checks on stg_trips, against the fake Redshift (no AWS)."""

import importlib
import pkgutil

import dagster as dg
import fakes
from fakes import FakeRedshift

import labpipes.defs
from labpipes.defs.marts import zone_hour_stats
from labpipes.defs.quality import RATE_RULES, duplicates, rate_checks, row_count
from labpipes.defs.staging import stg_trips
from labpipes.partitions import trip_months

CHECKS = [*rate_checks, row_count, duplicates]


class BadMonth(FakeRedshift):
    """A month with 3M rows where 2% of durations are <= 0 and 30% lack a passenger count."""

    def query(self, sql: str) -> list[dict]:
        fakes.LOG.append(sql)
        if "nonpositive_duration" in sql:
            return [{"rows": 3_000_000, "nonpositive_duration": 60_000, "over_3h": 0, "null_passengers": 900_000,
                     "unknown_zone": 0, "wrong_month": 0}]
        return super().query(sql)


def _run(redshift):
    instance = dg.DagsterInstance.ephemeral()
    instance.add_dynamic_partitions(trip_months.name, ["2024-01"])
    return dg.materialize(
        [stg_trips, *CHECKS, zone_hour_stats],
        partition_key="2024-01",
        instance=instance,
        resources={"redshift": redshift},
        selection=dg.AssetSelection.assets(stg_trips, zone_hour_stats),
        raise_on_error=False,
    )


def _evaluations(result) -> dict:
    return {e.check_name: e for e in result.get_asset_check_evaluations()}


def test_every_check_is_loaded_by_the_code_location(monkeypatch):
    for k in ("REDSHIFT_CLUSTER_ID", "REDSHIFT_DATABASE", "REDSHIFT_DB_USER", "LAB_DATA_BUCKET", "LAB_ARTIFACT_BUCKET",
              "MLFLOW_TRACKING_URI", "LLM_HOST"):
        monkeypatch.setenv(k, "x")
    for m in pkgutil.iter_modules(labpipes.defs.__path__):
        importlib.import_module(f"labpipes.defs.{m.name}")
    from labpipes.definitions import defs

    names = {k.name for k in defs.resolve_asset_graph().asset_check_keys}
    assert {r.name for r in RATE_RULES} | {"row_count", "duplicates", "schema_contract"} <= names


def test_a_normal_month_reaches_the_mart():
    fakes.LOG.clear()
    result = _run(FakeRedshift(cluster_id="lab", database="lab", db_user="labadmin", rows=3_000_000))
    assert all(e.passed for e in _evaluations(result).values())
    assert dg.AssetKey("zone_hour_stats") in {e.asset_key for e in result.get_asset_materialization_events()}


def test_a_broken_month_is_blocked_and_the_warning_does_not_block():
    fakes.LOG.clear()
    result = _run(BadMonth(cluster_id="lab", database="lab", db_user="labadmin", rows=3_000_000))
    checks = _evaluations(result)
    assert not checks["nonpositive_duration"].passed and checks["nonpositive_duration"].blocking
    assert not checks["null_passengers"].passed and not checks["null_passengers"].blocking
    assert dg.AssetKey("zone_hour_stats") not in {e.asset_key for e in result.get_asset_materialization_events()}


def test_a_truncated_month_is_blocked():
    fakes.LOG.clear()
    result = _run(FakeRedshift(cluster_id="lab", database="lab", db_user="labadmin", rows=500))
    assert not _evaluations(result)["row_count"].passed
