"""S1: the raw_trips asset: one month's file, COPYed into ingest.raw_trips.

Decisions (records in decisions/): COPY rather than Spectrum, so the data the checks see is the data we
loaded; sort key tpep_pickup_datetime with DISTSTYLE AUTO, on a single-node cluster.

Idempotent: re-running a month replaces its rows instead of adding a second copy. Everything runs as one
transaction (one RedshiftData.execute call), so a reader never sees the month half-deleted, and a failed
COPY leaves the previous load in place:

  1. COPY the file into a temporary landing table with exactly the contract's columns. Parquet COPY maps
     columns by position, so the landing table can't carry extra columns like source_file.
  2. Delete the month's rows (they carry the file name in source_file).
  3. Insert the landing rows, tagged with the file name.
"""

import dagster as dg

from ..contracts import redshift_columns, trips_schema
from ..partitions import source_file, trip_months
from ..resources import DataBucket, RedshiftData
from .trip_files import trip_files


def load_statements(uri: str, file: str) -> list[str]:
    """The SQL for one month's load, in order. `file` comes from partitions.source_file (validated)."""
    columns = redshift_columns(trips_schema())
    return [
        "create schema if not exists ingest",
        f"create table if not exists ingest.raw_trips ({columns}, source_file varchar(64) not null) "
        "diststyle auto sortkey (tpep_pickup_datetime)",
        f"create temp table trips_landing ({columns})",
        f"copy trips_landing from '{uri}' iam_role default format as parquet",
        f"delete from ingest.raw_trips where source_file = '{file}'",
        f"insert into ingest.raw_trips select *, '{file}' from trips_landing",
    ]


@dg.asset(partitions_def=trip_months, deps=[trip_files], group_name="ingest", kinds={"redshift"}, pool="redshift")
def raw_trips(context: dg.AssetExecutionContext, redshift: RedshiftData, data_bucket: DataBucket) -> dg.MaterializeResult:
    month = context.partition_key
    file = source_file(month)
    uri = data_bucket.trip_uri(month)
    redshift.execute(*load_statements(uri, file))
    rows = redshift.scalar(f"select count(*) from ingest.raw_trips where source_file = '{file}'")
    return dg.MaterializeResult(
        metadata={"rows": dg.MetadataValue.int(int(rows)), "source": uri},
    )
