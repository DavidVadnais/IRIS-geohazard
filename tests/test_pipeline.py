import dataclasses
from pathlib import Path

import pytest
import responses
from pyproj import Transformer
from responses import matchers
from sqlalchemy import text

from geohazard.config.data_sources import get_data_source
from geohazard.config.settings import HttpSettings
from geohazard.loaders.postgis_loader import PostgisLoader
from geohazard.runner import harvest_source
from geohazard.scrapers.arcgis_rest_scraper import ArcgisRestScraper
from geohazard.scrapers.base import LayerNotFoundError, ScraperError, build_session
from geohazard.scrapers.wfs_scraper import WfsScraper

pytestmark = pytest.mark.db

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "fixtures" / "hlnug_demo"
WFS = ROOT / "tests" / "fixtures" / "wfs"
ARCGIS = ROOT / "tests" / "fixtures" / "arcgis"

WFS_URL = "https://wfs.hlnug.test/wfs"
WFS_SOURCE = dataclasses.replace(get_data_source("hlnug_fixture_wfs"), url=WFS_URL)
LAYER = WFS_SOURCE.layers[0].name
HTTP = HttpSettings(max_retries=0, page_size=1000)


def mock_wfs(
    getfeature: dict[int, Path],
    capabilities: Path = DEMO / "capabilities.xml",
    status: int = 200,
) -> None:
    def add(
        request: str,
        body: bytes,
        content_type: str,
        extra: dict | None = None,
        code: int = 200,
    ) -> None:
        responses.get(
            WFS_URL,
            body=body,
            status=code,
            content_type=content_type,
            match=[
                matchers.query_param_matcher(
                    {"request": request, **(extra or {})}, strict_match=False
                )
            ],
        )

    add("GetCapabilities", capabilities.read_bytes(), "application/xml")
    add(
        "DescribeFeatureType",
        (DEMO / "describefeaturetype.xsd").read_bytes(),
        "application/xml",
    )
    for start_index, path in getfeature.items():
        content_type = (
            "application/json"
            if path.suffix == ".json"
            else "application/gml+xml; version=3.2"
        )
        add(
            "GetFeature",
            path.read_bytes(),
            content_type,
            {"startIndex": str(start_index)},
            status,
        )


def harvest(engine, http: HttpSettings = HTTP, source=WFS_SOURCE):
    scraper = WfsScraper(source, build_session(http), http)
    return harvest_source(source, scraper, PostgisLoader(engine))


def query(engine, sql: str, **params):
    with engine.connect() as connection:
        return connection.execute(text(sql), params).all()


def restrictions(engine):
    return {
        row.source_feature_id: row
        for row in query(
            engine,
            "SELECT source_feature_id, restriction_category, status, hazard_classification, classification_status, "
            "geom_was_repaired, source_crs, ST_SRID(geom) AS srid, ST_X(ST_PointOnSurface(geom)) AS x, "
            "ST_Y(ST_PointOnSurface(geom)) AS y, source_url, layer_name, license, fetched_at, content_hash, "
            "last_changed_at, last_run_id FROM iris_staging.mining_restrictions",
        )
    }


def rejected(engine):
    return {
        (row.source_feature_id, row.reason_code)
        for row in query(
            engine,
            "SELECT source_feature_id, reason_code FROM iris_staging.mining_restrictions_rejected",
        )
    }


def runs(engine):
    return query(
        engine,
        "SELECT status, error, access_constraints, fees, data_currency FROM iris_staging.harvest_runs",
    )


@responses.activate
def test_valid_features(engine):
    mock_wfs({0: WFS / "valid.json"})
    counts = harvest(engine)

    assert (counts.fetched, counts.inserted, counts.rejected) == (4, 4, 0)
    rows = restrictions(engine)
    assert {k: r.restriction_category for k, r in rows.items()} == {
        "B-1": "mine_right",
        "S-1": "shaft",
        "H-1": "cavity",
        "A-1": "contaminated_land",
    }
    for feature_id, row in rows.items():
        assert row.srid == 25832
        assert row.layer_name == LAYER
        assert row.source_url.startswith(WFS_URL + "?")
        assert f"resourceId={feature_id}" in row.source_url
        assert row.license and row.fetched_at is not None
    [run] = runs(engine)
    assert run.status == "succeeded"
    assert run.access_constraints == "none (synthetic fixture data)"
    assert run.fees == "none (synthetic)"
    assert run.data_currency == "not stated by service"


@responses.activate
def test_null_geometry(engine):
    mock_wfs({0: WFS / "null_geometry.json"})
    counts = harvest(engine)

    assert (counts.inserted, counts.rejected) == (1, 1)
    assert rejected(engine) == {("N-1", "null_geometry")}
    assert set(restrictions(engine)) == {"S-1"}


@responses.activate
def test_invalid_geometry(engine):
    mock_wfs({0: WFS / "invalid_geometry.json"})
    harvest(engine)

    rows = restrictions(engine)
    assert set(rows) == {"BT-1"}
    assert rows["BT-1"].geom_was_repaired is True
    assert (
        query(
            engine,
            "SELECT bool_and(ST_IsValid(geom)) FROM iris_staging.mining_restrictions",
        )[0][0]
        is True
    )
    assert rejected(engine) == {("CL-1", "invalid_geometry_unrepairable")}
    [raw_wkt] = query(
        engine, "SELECT raw_geometry_wkt FROM iris_staging.mining_restrictions_rejected"
    )[0]
    assert raw_wkt.startswith("POLYGON")


@responses.activate
def test_wrong_crs_is_reprojected(engine):
    mock_wfs({0: WFS / "wrong_crs_31467.json"})
    harvest(engine)

    expected_x, expected_y = Transformer.from_crs(
        31467, 25832, always_xy=True
    ).transform(3534076.43, 5686872.17)
    row = restrictions(engine)["GK-1"]
    assert row.source_crs == "EPSG:31467"
    assert row.srid == 25832
    assert row.x == pytest.approx(expected_x, abs=0.002)
    assert row.y == pytest.approx(expected_y, abs=0.002)


@responses.activate
def test_missing_crs_is_rejected(engine):
    mock_wfs({0: WFS / "no_crs.json"})
    counts = harvest(engine)

    assert (counts.inserted, counts.rejected) == (0, 2)
    assert rejected(engine) == {("NC-1", "unknown_crs"), ("NC-2", "unknown_crs")}


@responses.activate
def test_missing_classification(engine):
    mock_wfs({0: WFS / "missing_classification.json"})
    harvest(engine)

    rows = restrictions(engine)
    for feature_id in ("M-1", "M-2", "M-3", "M-4"):
        assert rows[feature_id].hazard_classification is None
        assert rows[feature_id].classification_status == "missing"
    assert (rows["P-1"].hazard_classification, rows["P-1"].classification_status) == (
        "gering",
        "published",
    )
    [(defaulted,)] = query(
        engine,
        "SELECT count(*) FROM iris_staging.mining_restrictions "
        "WHERE source_feature_id LIKE 'M-%' AND hazard_classification IS NOT NULL",
    )
    assert defaulted == 0


@responses.activate
def test_duplicate_run_produces_no_changes(engine):
    mock_wfs({0: WFS / "valid.json"})
    first = harvest(engine)
    before = restrictions(engine)

    second = harvest(engine)

    assert (first.inserted, first.updated) == (4, 0)
    assert (second.inserted, second.updated, second.unchanged) == (0, 0, 4)
    assert restrictions(engine) == before
    assert (
        query(engine, "SELECT count(*) FROM iris_staging.mining_restrictions")[0][0]
        == 4
    )
    assert [r.status for r in runs(engine)] == ["succeeded", "succeeded"]


@responses.activate
def test_pagination(engine):
    mock_wfs({0: WFS / "page_0.json", 2: WFS / "page_2.json", 4: WFS / "page_4.json"})
    counts = harvest(engine, HttpSettings(max_retries=0, page_size=2))

    assert (counts.fetched, counts.inserted) == (5, 5)
    get_feature_calls = [
        c for c in responses.calls if "request=GetFeature" in c.request.url
    ]
    assert len(get_feature_calls) == 3


@responses.activate
def test_gml_fallback_when_json_not_offered(engine):
    mock_wfs(
        {0: WFS / "getfeature.gml"}, capabilities=WFS / "capabilities_gml_only.xml"
    )
    counts = harvest(engine)

    assert (counts.inserted, counts.rejected) == (2, 0)
    rows = restrictions(engine)
    assert rows["G-1"].restriction_category == "shaft"
    assert rows["G-2"].restriction_category == "contaminated_land"
    assert rows["G-2"].classification_status == "missing"
    assert (
        "outputFormat=application%2Fgml%2Bxml"
        in next(c for c in responses.calls if "GetFeature" in c.request.url).request.url
    )


@responses.activate
def test_http_error_marks_run_failed_and_loads_nothing(engine):
    mock_wfs({0: WFS / "valid.json"}, status=500)
    with pytest.raises(ScraperError, match="HTTP 500"):
        harvest(engine)

    [run] = runs(engine)
    assert run.status == "failed"
    assert "HTTP 500" in run.error
    assert restrictions(engine) == {}


@responses.activate
def test_missing_layer_fails_discovery_visibly(engine):
    mock_wfs({}, capabilities=WFS / "capabilities_missing_layer.xml")
    with pytest.raises(LayerNotFoundError):
        harvest(engine)

    [run] = runs(engine)
    assert run.status == "failed"
    assert "discovery failed" in run.error


@responses.activate
def test_arcgis_rest_paging_and_metadata(engine):
    source = get_data_source("hlnug_geogefahren_erdfaelle")
    responses.get(
        source.url,
        body=(ARCGIS / "service.json").read_bytes(),
        content_type="application/json",
    )
    responses.get(
        f"{source.url}/20",
        body=(ARCGIS / "layer_20.json").read_bytes(),
        content_type="application/json",
    )
    for offset in (0, 2):
        responses.get(
            f"{source.url}/20/query",
            body=(ARCGIS / f"query_{offset}.json").read_bytes(),
            content_type="application/json",
            match=[
                matchers.query_param_matcher(
                    {"resultOffset": str(offset), "outSR": "25832"}, strict_match=False
                )
            ],
        )
    scraper = ArcgisRestScraper(source, build_session(HTTP), HTTP)
    counts = harvest_source(source, scraper, PostgisLoader(engine))

    assert (counts.fetched, counts.inserted) == (3, 3)
    rows = restrictions(engine)
    assert {r.restriction_category for r in rows.values()} == {"sinkhole_subsidence"}
    assert {r.classification_status for r in rows.values()} == {"missing"}
    [run] = runs(engine)
    assert run.data_currency == "lastEditDate 2025-01-01T00:00:00+00:00"
    assert "© HLNUG (test)" in run.access_constraints
