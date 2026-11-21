"""S1: schema_contract, raw_trips and the sensor, against the fakes (no AWS)."""

import json

import dagster as dg
import fakes
from fakes import fake_bucket, fake_redshift, schema_2023, schema_2025

from labpipes.contracts import trips_schema
from labpipes.defs.raw_trips import load_statements, raw_trips
from labpipes.defs.schema_check import schema_contract, schema_differences
from labpipes.defs.sensor import trip_files_sensor
from labpipes.defs.trip_files import trip_files
from labpipes.partitions import trip_months


def test_contract_schema_has_no_differences():
    assert schema_differences(trips_schema(), trips_schema()) == []


def test_name_case_alone_is_not_a_difference():
    lowered = [type(c)(c.name.lower(), c.type) for c in trips_schema()]
    assert schema_differences(trips_schema(), lowered) == []


def test_appended_column_is_a_difference():
    problems = schema_differences(trips_schema(), schema_2025())
    assert any("cbd_congestion_fee" in p and "not in the contract" in p for p in problems)


def test_changed_types_are_differences():
    problems = schema_differences(trips_schema(), schema_2023())
    assert any("VendorID" in p and "INT64" in p for p in problems)
    assert not any("airport_fee" in p.lower() and "contract has Airport_fee" in p for p in problems)


def test_load_replaces_the_month_in_one_transaction():
    sqls = load_statements("s3://data/trips/yellow_tripdata_2024-01.parquet", "yellow_tripdata_2024-01.parquet")
    copy = next(i for i, s in enumerate(sqls) if s.startswith("copy trips_landing"))
    delete = next(i for i, s in enumerate(sqls) if s.startswith("delete from ingest.raw_trips"))
    insert = next(i for i, s in enumerate(sqls) if s.startswith("insert into ingest.raw_trips"))
    assert copy < delete < insert
    assert "sortkey (tpep_pickup_datetime)" in sqls[1] and "diststyle auto" in sqls[1]


def _ingest(schema):
    instance = dg.DagsterInstance.ephemeral()
    instance.add_dynamic_partitions(trip_months.name, ["2024-01"])
    redshift = fake_redshift()
    result = dg.materialize(
        [trip_files, schema_contract, raw_trips],
        partition_key="2024-01",
        instance=instance,
        resources={"redshift": redshift, "data_bucket": fake_bucket({"2024-01": ("e1", schema)})},
        raise_on_error=False,
    )
    return result


def test_matching_file_is_copied():
    result = _ingest(trips_schema())
    assert result.success
    (check,) = result.get_asset_check_evaluations()
    assert check.passed
    assert any(s.startswith("copy trips_landing") for s in fakes.LOG)


def test_moved_schema_blocks_the_copy():
    result = _ingest(schema_2025())
    (check,) = result.get_asset_check_evaluations()
    assert not check.passed
    assert not any(s.startswith("copy") for s in fakes.LOG)
    assert dg.AssetKey("raw_trips") not in {e.asset_key for e in result.get_asset_materialization_events()}


def _tick(files: dict, cursor: str | None = None):
    instance = dg.DagsterInstance.ephemeral()
    ctx = dg.build_sensor_context(
        instance=instance, cursor=cursor, resources={"data_bucket": fake_bucket({m: (e, trips_schema()) for m, e in files.items()})}
    )
    return trip_files_sensor(ctx)


def test_sensor_requests_new_months_once():
    first = _tick({"2024-01": "a", "2024-02": "b"})
    assert [r.partition_key for r in first.run_requests] == ["2024-01", "2024-02"]
    assert first.dynamic_partitions_requests[0].partition_keys == ["2024-01", "2024-02"]
    again = _tick({"2024-01": "a", "2024-02": "b"}, cursor=first.cursor)
    assert isinstance(again, dg.SkipReason)


def test_sensor_reruns_a_changed_file_without_a_new_partition():
    cursor = json.dumps({"2024-01": "a"})
    result = _tick({"2024-01": "a2"}, cursor=cursor)
    assert [(r.partition_key, r.run_key) for r in result.run_requests] == [("2024-01", "2024-01:a2")]
    assert result.dynamic_partitions_requests == []
