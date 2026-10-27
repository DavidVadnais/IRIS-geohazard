"""create iris_staging mining restriction tables

Revision ID: 0001
Revises:
Create Date: 2026-10-27
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

CATEGORIES = ("mine_right", "shaft", "cavity", "contaminated_land", "sinkhole_subsidence", "extraction_site")
REJECT_REASONS = (
    "null_geometry",
    "empty_geometry",
    "invalid_geometry_unrepairable",
    "unsupported_geometry_type",
    "unknown_crs",
    "missing_feature_id",
    "duplicate_feature_id",
    "out_of_region",
    "unmapped_category",
)


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE SCHEMA IF NOT EXISTS iris_staging")

    op.execute(
        """
        CREATE TABLE iris_staging.harvest_runs (
            run_id uuid PRIMARY KEY,
            source_key text NOT NULL,
            source text NOT NULL,
            mode text NOT NULL CHECK (mode IN ('live', 'fixture')),
            status text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'succeeded', 'failed')),
            started_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            source_url text NOT NULL,
            layer_names text[] NOT NULL,
            license text NOT NULL,
            attribution text,
            access_constraints text,
            fees text,
            data_currency text,
            capabilities_hash char(64),
            fetched_count integer NOT NULL DEFAULT 0,
            inserted_count integer NOT NULL DEFAULT 0,
            updated_count integer NOT NULL DEFAULT 0,
            unchanged_count integer NOT NULL DEFAULT 0,
            rejected_count integer NOT NULL DEFAULT 0,
            error text
        )
        """
    )

    op.execute(
        f"""
        CREATE TABLE iris_staging.mining_restrictions (
            id bigserial PRIMARY KEY,
            source text NOT NULL,
            source_feature_id text NOT NULL,
            restriction_category text NOT NULL CHECK (restriction_category IN ({_sql_list(CATEGORIES)})),
            hazard_type text,
            status text,
            status_raw text,
            hazard_classification text,
            classification_status text NOT NULL
                CHECK (classification_status IN ('published', 'missing', 'unrecognised')),
            attributes jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            geom geometry(Geometry, 25832) NOT NULL,
            geom_type text NOT NULL,
            geom_was_repaired boolean NOT NULL DEFAULT false,
            source_crs text NOT NULL,
            content_hash char(64) NOT NULL,
            source_url text NOT NULL,
            layer_name text NOT NULL,
            license text NOT NULL,
            fetched_at timestamptz NOT NULL,
            first_seen_at timestamptz NOT NULL DEFAULT now(),
            last_changed_at timestamptz NOT NULL DEFAULT now(),
            last_run_id uuid NOT NULL REFERENCES iris_staging.harvest_runs (run_id),
            CONSTRAINT mining_restrictions_source_feature_uq UNIQUE (source, source_feature_id),
            CONSTRAINT mining_restrictions_classification_consistent CHECK (
                (classification_status = 'missing' AND hazard_classification IS NULL)
                OR (classification_status <> 'missing' AND hazard_classification IS NOT NULL)
            ),
            CONSTRAINT mining_restrictions_geom_valid CHECK (ST_IsValid(geom) AND NOT ST_IsEmpty(geom))
        )
        """
    )
    op.execute("CREATE INDEX mining_restrictions_geom_gist ON iris_staging.mining_restrictions USING gist (geom)")
    op.execute(
        "CREATE INDEX mining_restrictions_category_idx ON iris_staging.mining_restrictions (restriction_category)"
    )
    op.execute(
        "CREATE INDEX mining_restrictions_classification_idx "
        "ON iris_staging.mining_restrictions (classification_status)"
    )

    op.execute(
        f"""
        CREATE TABLE iris_staging.mining_restrictions_rejected (
            id bigserial PRIMARY KEY,
            run_id uuid NOT NULL REFERENCES iris_staging.harvest_runs (run_id),
            source text NOT NULL,
            layer_name text NOT NULL,
            source_feature_id text,
            reason_code text NOT NULL CHECK (reason_code IN ({_sql_list(REJECT_REASONS)})),
            reason_detail text,
            raw_feature jsonb,
            raw_geometry_wkt text,
            source_url text NOT NULL,
            fetched_at timestamptz NOT NULL,
            rejected_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX mining_restrictions_rejected_run_idx ON iris_staging.mining_restrictions_rejected (run_id)"
    )
    op.execute(
        "CREATE INDEX mining_restrictions_rejected_reason_idx "
        "ON iris_staging.mining_restrictions_rejected (reason_code)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS iris_staging.mining_restrictions_rejected")
    op.execute("DROP TABLE IF EXISTS iris_staging.mining_restrictions")
    op.execute("DROP TABLE IF EXISTS iris_staging.harvest_runs")
