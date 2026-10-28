from datetime import UTC, datetime

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon
from sqlalchemy import text

from geohazard.config.data_sources import get_data_source
from geohazard.loaders.postgis_loader import PostgisLoader, RunMetadata
from geohazard.transformers.hazard_transformer import FetchContext, HazardTransformer

pytestmark = pytest.mark.db

SOURCE = get_data_source("hlnug_fixture_wfs")
KASSEL = (534_000.0, 5_685_000.0)


def transform(rows, fetched_at):
    frame = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:25832")
    context = FetchContext(source=SOURCE, layer=SOURCE.layers[0], fetched_at=fetched_at)
    return HazardTransformer(context).transform(frame)


def rows(status_s2="stillgelegt"):
    x, y = KASSEL
    return [
        {"OBJEKT_ID": "S-1", "OBJEKTART": "Schacht", "STATUS": "stillgelegt", "GEFAEHRDUNGSKLASSE": "hoch", "geometry": Point(x, y)},
        {"OBJEKT_ID": "S-2", "OBJEKTART": "Schacht", "STATUS": status_s2, "GEFAEHRDUNGSKLASSE": None, "geometry": Point(x + 100, y)},
        {
            "OBJEKT_ID": "H-1",
            "OBJEKTART": "Kaverne",
            "STATUS": "aktiv",
            "GEFAEHRDUNGSKLASSE": "mittel",
            "geometry": Polygon([(x, y), (x + 50, y), (x + 50, y + 50), (x, y + 50)]),
        },
        {"OBJEKT_ID": "N-1", "OBJEKTART": "Schacht", "STATUS": "aktiv", "GEFAEHRDUNGSKLASSE": "hoch", "geometry": None},
    ]


def run(loader, fetched_at, status_s2="stillgelegt"):
    result = transform(rows(status_s2), fetched_at)
    metadata = RunMetadata(
        source_key=SOURCE.key,
        source=SOURCE.source,
        mode=SOURCE.mode.value,
        source_url=SOURCE.url,
        layer_names=[SOURCE.layers[0].name],
        license=SOURCE.license,
    )
    run_id = loader.start_run(metadata)
    counts = loader.load(run_id, result.staged, result.rejected, fetched_count=len(rows()))
    loader.finish_run(run_id, counts)
    return run_id, counts


def snapshot(engine):
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT source_feature_id, content_hash, last_changed_at, last_run_id, fetched_at "
                "FROM iris_staging.mining_restrictions ORDER BY source_feature_id"
            )
        ).all()


def test_first_load_inserts_and_records_rejects(engine):
    run_id, counts = run(PostgisLoader(engine), datetime(2026, 10, 27, tzinfo=UTC))

    assert (counts.inserted, counts.updated, counts.unchanged, counts.rejected) == (3, 0, 0, 1)
    with engine.connect() as connection:
        rejected = connection.execute(
            text("SELECT source_feature_id, reason_code, run_id FROM iris_staging.mining_restrictions_rejected")
        ).all()
        stored = connection.execute(
            text(
                "SELECT source_feature_id, ST_SRID(geom), ST_IsValid(geom), hazard_classification, classification_status "
                "FROM iris_staging.mining_restrictions ORDER BY source_feature_id"
            )
        ).all()
        run_row = connection.execute(
            text("SELECT status, inserted_count, rejected_count FROM iris_staging.harvest_runs WHERE run_id = :r"),
            {"r": run_id},
        ).one()
    assert rejected == [("N-1", "null_geometry", run_id)]
    assert stored == [
        ("H-1", 25832, True, "mittel", "published"),
        ("S-1", 25832, True, "hoch", "published"),
        ("S-2", 25832, True, None, "missing"),
    ]
    assert tuple(run_row) == ("succeeded", 3, 1)


def test_duplicate_run_changes_nothing(engine):
    loader = PostgisLoader(engine)
    run(loader, datetime(2026, 10, 27, tzinfo=UTC))
    before = snapshot(engine)

    _, counts = run(loader, datetime(2026, 10, 28, tzinfo=UTC))

    assert (counts.inserted, counts.updated, counts.unchanged) == (0, 0, 3)
    assert snapshot(engine) == before


def test_changed_feature_is_updated_once(engine):
    loader = PostgisLoader(engine)
    first_run, _ = run(loader, datetime(2026, 10, 27, tzinfo=UTC))
    before = {row.source_feature_id: row for row in snapshot(engine)}

    second_run, counts = run(loader, datetime(2026, 10, 28, tzinfo=UTC), status_s2="verfüllt")

    assert (counts.inserted, counts.updated, counts.unchanged) == (0, 1, 2)
    after = {row.source_feature_id: row for row in snapshot(engine)}
    assert len(after) == 3
    assert after["S-2"].content_hash != before["S-2"].content_hash
    assert after["S-2"].last_run_id == second_run
    assert after["S-1"] == before["S-1"]
    assert after["S-1"].last_run_id == first_run
