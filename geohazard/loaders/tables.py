from geoalchemy2 import Geometry
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB

STAGING_SCHEMA = "iris_staging"

metadata = MetaData(schema=STAGING_SCHEMA)

harvest_runs = Table(
    "harvest_runs",
    metadata,
    Column("run_id", Uuid, primary_key=True),
    Column("source_key", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("mode", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("source_url", Text, nullable=False),
    Column("layer_names", ARRAY(Text), nullable=False),
    Column("license", Text, nullable=False),
    Column("attribution", Text),
    Column("access_constraints", Text),
    Column("fees", Text),
    Column("data_currency", Text),
    Column("capabilities_hash", Text),
    Column("fetched_count", Integer),
    Column("inserted_count", Integer),
    Column("updated_count", Integer),
    Column("unchanged_count", Integer),
    Column("rejected_count", Integer),
    Column("error", Text),
)

mining_restrictions = Table(
    "mining_restrictions",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("source", Text, nullable=False),
    Column("source_feature_id", Text, nullable=False),
    Column("restriction_category", Text, nullable=False),
    Column("hazard_type", Text),
    Column("status", Text),
    Column("status_raw", Text),
    Column("hazard_classification", Text),
    Column("classification_status", Text, nullable=False),
    Column("attributes", JSONB, nullable=False),
    Column("geom", Geometry(srid=25832, spatial_index=False), nullable=False),
    Column("geom_type", Text, nullable=False),
    Column("geom_was_repaired", Boolean, nullable=False),
    Column("source_crs", Text, nullable=False),
    Column("content_hash", Text, nullable=False),
    Column("source_url", Text, nullable=False),
    Column("layer_name", Text, nullable=False),
    Column("license", Text, nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("first_seen_at", DateTime(timezone=True)),
    Column("last_changed_at", DateTime(timezone=True)),
    Column("last_run_id", Uuid, nullable=False),
)

mining_restrictions_rejected = Table(
    "mining_restrictions_rejected",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("run_id", Uuid, nullable=False),
    Column("source", Text, nullable=False),
    Column("layer_name", Text, nullable=False),
    Column("source_feature_id", Text),
    Column("reason_code", Text, nullable=False),
    Column("reason_detail", Text),
    Column("raw_feature", JSONB),
    Column("raw_geometry_wkt", Text),
    Column("source_url", Text, nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("rejected_at", DateTime(timezone=True)),
)
