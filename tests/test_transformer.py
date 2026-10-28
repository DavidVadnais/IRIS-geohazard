import logging
from datetime import UTC, datetime

import geopandas as gpd
import pytest
from pyproj import Transformer
from shapely.geometry import GeometryCollection, Point, Polygon

from geohazard.config.data_sources import get_data_source
from geohazard.transformers.hazard_transformer import FetchContext, HazardTransformer, classify

FETCHED_AT = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
FRANKFURT_25832 = (477_000.0, 5_552_000.0)
KASSEL_25832 = (534_000.0, 5_685_000.0)


def demo_transformer(fetched_at: datetime = FETCHED_AT) -> HazardTransformer:
    source = get_data_source("hlnug_fixture_wfs")
    return HazardTransformer(FetchContext(source=source, layer=source.layers[0], fetched_at=fetched_at))


def feature(feature_id, geometry, kind="Schacht", status="stillgelegt", klasse="hoch", **extra):
    return {"OBJEKT_ID": feature_id, "OBJEKTART": kind, "STATUS": status, "GEFAEHRDUNGSKLASSE": klasse, "geometry": geometry, **extra}


def frame(rows, crs="EPSG:25832"):
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)


def square(x, y, size=50.0):
    return Polygon([(x, y), (x + size, y), (x + size, y + size), (x, y + size)])


def reasons(result):
    return {(r.source_feature_id, r.reason_code) for r in result.rejected}


def test_valid_features_are_mapped_with_provenance():
    rows = [
        feature("S-1", Point(*KASSEL_25832)),
        feature("B-1", square(*FRANKFURT_25832), kind="Bergbauberechtigung", status="erloschen", klasse="gering"),
        feature("H-1", square(*KASSEL_25832), kind="Kaverne", status="verfüllt", klasse="mittel"),
        feature("A-1", square(*FRANKFURT_25832), kind="Altablagerung", status="in Bearbeitung", klasse="Keine Gefährdung bekannt"),
    ]
    result = demo_transformer().transform(frame(rows))

    assert result.rejected == []
    by_id = {r.source_feature_id: r for r in result.staged}
    assert {k: v.restriction_category for k, v in by_id.items()} == {
        "S-1": "shaft",
        "B-1": "mine_right",
        "H-1": "cavity",
        "A-1": "contaminated_land",
    }
    assert by_id["H-1"].status == "backfilled"
    assert by_id["H-1"].status_raw == "verfüllt"
    assert by_id["A-1"].hazard_classification == "Keine Gefährdung bekannt"
    assert by_id["A-1"].classification_status == "published"
    for record in result.staged:
        assert record.source == "hlnug_demo"
        assert record.layer_name == "hlnug_demo:he_bergbau_kavernen"
        assert record.source_crs == "EPSG:25832"
        assert f"resourceId={record.source_feature_id}" in record.source_url
        assert record.license
        assert record.fetched_at == FETCHED_AT
        assert len(record.content_hash) == 64


def test_null_and_empty_geometry_are_rejected_and_logged(caplog):
    caplog.set_level(logging.INFO)
    rows = [feature("N-1", None), feature("E-1", Polygon()), feature("OK-1", Point(*KASSEL_25832))]
    result = demo_transformer().transform(frame(rows))

    assert reasons(result) == {("N-1", "null_geometry"), ("E-1", "empty_geometry")}
    assert [r.source_feature_id for r in result.staged] == ["OK-1"]
    assert "feature=N-1 reason=null_geometry" in caplog.text
    assert "feature=E-1 reason=empty_geometry" in caplog.text


def test_invalid_geometry_is_repaired_or_rejected(caplog):
    caplog.set_level(logging.INFO)
    x, y = KASSEL_25832
    bow_tie = Polygon([(x, y), (x + 100, y + 100), (x + 100, y), (x, y + 100), (x, y)])
    collapsed = Polygon([(x, y), (x + 10, y + 10), (x + 20, y + 20), (x, y)])
    rows = [feature("BT-1", bow_tie, kind="Hohlraum"), feature("CL-1", collapsed, kind="Hohlraum")]
    result = demo_transformer().transform(frame(rows))

    assert reasons(result) == {("CL-1", "invalid_geometry_unrepairable")}
    [repaired] = result.staged
    assert repaired.source_feature_id == "BT-1"
    assert repaired.geom_was_repaired is True
    assert repaired.geom.is_valid
    assert repaired.geom.geom_type == "MultiPolygon"
    assert "repaired invalid geometry" in caplog.text


def test_unsupported_geometry_type_is_rejected():
    rows = [feature("GC-1", GeometryCollection([Point(*KASSEL_25832)]))]
    result = demo_transformer().transform(frame(rows))
    assert reasons(result) == {("GC-1", "unsupported_geometry_type")}


def test_gauss_krueger_input_is_reprojected_to_25832():
    to_31467 = Transformer.from_crs(25832, 31467, always_xy=True)
    gk_x, gk_y = to_31467.transform(*KASSEL_25832)
    result = demo_transformer().transform(frame([feature("GK-1", Point(gk_x, gk_y))], crs="EPSG:31467"))

    [record] = result.staged
    assert record.source_crs == "EPSG:31467"
    assert record.geom.x == pytest.approx(KASSEL_25832[0], abs=0.01)
    assert record.geom.y == pytest.approx(KASSEL_25832[1], abs=0.01)


def test_missing_crs_is_rejected_not_assumed():
    result = demo_transformer().transform(frame([feature("NC-1", Point(*KASSEL_25832))], crs=None))
    assert reasons(result) == {("NC-1", "unknown_crs")}
    assert result.staged == []


def test_unaccepted_crs_is_rejected():
    result = demo_transformer().transform(frame([feature("WM-1", Point(966_000, 6_464_000))], crs="EPSG:3857"))
    assert reasons(result) == {("WM-1", "unknown_crs")}


def test_swapped_axis_wgs84_is_caught_as_out_of_region():
    rows = [feature("LL-1", Point(8.68, 50.11)), feature("SW-1", Point(50.11, 8.68))]
    result = demo_transformer().transform(frame(rows, crs="EPSG:4326"))

    assert [r.source_feature_id for r in result.staged] == ["LL-1"]
    assert reasons(result) == {("SW-1", "out_of_region")}


@pytest.mark.parametrize("raw", [None, "", "  ", "unbekannt", "Unbekannt", "k.A.", "n.b.", "-", float("nan")])
def test_missing_classification_is_null_and_never_safe(raw):
    result = demo_transformer().transform(frame([feature("MC-1", Point(*KASSEL_25832), klasse=raw)]))

    [record] = result.staged
    assert record.hazard_classification is None
    assert record.classification_status == "missing"


def test_classification_vocabulary_handling():
    vocabulary = get_data_source("hlnug_fixture_wfs").classification_vocabulary
    assert classify("gering", vocabulary) == ("gering", "published")
    assert classify("Klasse 7", vocabulary) == ("Klasse 7", "unrecognised")
    assert classify(None, vocabulary) == (None, "missing")


def test_live_layer_without_classification_field_is_missing():
    source = get_data_source("hlnug_geogefahren_erdfaelle")
    transformer = HazardTransformer(FetchContext(source=source, layer=source.layers[0], fetched_at=FETCHED_AT))
    rows = [{"OBJECTID": 7, "AMT_NR": 4711, "TS_FLAE": 12.5, "geometry": Point(*KASSEL_25832)}]
    [record] = transformer.transform(frame(rows)).staged

    assert record.restriction_category == "sinkhole_subsidence"
    assert record.classification_status == "missing"
    assert record.hazard_classification is None
    assert record.source_url.startswith(source.url + "/20/query?")


def test_unmapped_category_missing_id_and_duplicate_id_are_rejected():
    rows = [
        feature("U-1", Point(*KASSEL_25832), kind="Steinbruch"),
        feature(None, Point(*KASSEL_25832)),
        feature("D-1", Point(*KASSEL_25832)),
        feature("D-1", Point(*FRANKFURT_25832)),
    ]
    result = demo_transformer().transform(frame(rows))

    assert reasons(result) == {
        ("U-1", "unmapped_category"),
        (None, "missing_feature_id"),
        ("D-1", "duplicate_feature_id"),
    }
    [kept] = result.staged
    assert kept.geom.x == pytest.approx(KASSEL_25832[0])


def test_content_hash_is_stable_across_runs_and_sensitive_to_changes():
    rows = [feature("S-1", square(*KASSEL_25832))]
    first = demo_transformer(FETCHED_AT).transform(frame(rows)).staged[0]
    second = demo_transformer(datetime(2026, 11, 1, tzinfo=UTC)).transform(frame(rows)).staged[0]
    changed = demo_transformer().transform(frame([feature("S-1", square(*KASSEL_25832), status="verfüllt")])).staged[0]
    moved = demo_transformer().transform(frame([feature("S-1", square(KASSEL_25832[0] + 1, KASSEL_25832[1]))])).staged[0]

    assert first.content_hash == second.content_hash
    assert changed.content_hash != first.content_hash
    assert moved.content_hash != first.content_hash
