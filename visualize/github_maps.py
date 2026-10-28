import json
import logging
import re
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import mapping

logger = logging.getLogger("visualize.github_maps")

COORDINATE_DECIMALS = 6
EXAMPLE_PARCEL = "297"
EXAMPLE_RADIUS_M = 250
MISSING_CLASSIFICATION = "MISSING (not published, not safe)"

EXPORT_SQL = """
SELECT source, source_feature_id, restriction_category, hazard_type, status,
       hazard_classification, classification_status, source_url, geom
FROM iris_staging.mining_restrictions
ORDER BY source, source_feature_id
"""

ATTRIBUTION = (
    "Restrictions: © HLNUG (CC BY 4.0, assumed) and synthetic IRIS fixture data; "
    "Flurstücke: HVBG INSPIRE-WFS ALKIS, dl-de/zero-2.0"
)


def round_coordinates(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, COORDINATE_DECIMALS)
    if isinstance(value, list | tuple):
        return [round_coordinates(item) for item in value]
    return value


def geometry_json(geometry) -> dict[str, Any]:
    shape = mapping(geometry)
    return {
        "type": shape["type"],
        "coordinates": round_coordinates(shape["coordinates"]),
    }


def present(value: Any) -> Any:
    return None if pd.isna(value) else value


def classification_text(row) -> str:
    if row.classification_status == "missing":
        return MISSING_CLASSIFICATION
    if row.classification_status == "unrecognised":
        return f"{row.hazard_classification} (unrecognised value)"
    return row.hazard_classification


def restriction_feature(row, styles: dict[str, tuple[str, str]]) -> dict[str, Any]:
    label, color = styles[row.restriction_category]
    properties = {
        "title": f"{label}: {row.source_feature_id}",
        "category": row.restriction_category,
        "feature_id": row.source_feature_id,
        "source": row.source,
        "type": present(row.hazard_type),
        "status": present(row.status),
        "classification": classification_text(row),
        "source_url": row.source_url,
    }
    if row.geometry.geom_type in ("Point", "MultiPoint"):
        properties |= {"marker-color": color, "marker-size": "small"}
    else:
        properties |= {
            "stroke": color,
            "stroke-width": 2,
            "fill": color,
            "fill-opacity": 0.4,
        }
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": geometry_json(row.geometry),
    }


def parcel_feature(row, color: str) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {
            "title": f"Flurstück {row.flurstueck} (ALKIS example parcel)",
            "flurstueck": row.flurstueck,
            "national_cadastral_reference": row.national_cadastral_reference,
            "official_area_m2": row.official_area_m2,
            "license": row.license,
            "source_service": row.source_service,
            "stroke": color,
            "stroke-width": 3,
            "fill": color,
            "fill-opacity": 0.05,
        },
        "geometry": geometry_json(row.geometry),
    }


def feature_collection(name: str, features: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "name": name,
        "attribution": ATTRIBUTION,
        "features": features,
    }


def load_export_frame(engine) -> gpd.GeoDataFrame:
    return gpd.read_postgis(EXPORT_SQL, engine, geom_col="geom").rename_geometry(
        "geometry"
    )


def build_collections(
    restrictions: gpd.GeoDataFrame,
    parcels: gpd.GeoDataFrame,
    styles: dict[str, tuple[str, str]],
    parcel_color: str,
) -> dict[str, dict[str, Any]]:
    wgs84 = restrictions.to_crs(4326)
    everything = feature_collection(
        "hesse_restrictions",
        [restriction_feature(row, styles) for row in wgs84.itertuples()],
    )
    synthetic = feature_collection(
        "synthetic_wfs_demonstrator",
        [
            restriction_feature(row, styles)
            for row in wgs84[wgs84["source"] == "hlnug_demo"].itertuples()
        ],
    )
    parcel = parcels[parcels["flurstueck"] == EXAMPLE_PARCEL]
    example_features = []
    if parcel.empty:
        logger.warning(
            "example parcel %s not found; parcel map left empty", EXAMPLE_PARCEL
        )
    else:
        parcel_area = parcel.to_crs(restrictions.crs).geometry.iloc[0]
        nearby = restrictions.geometry.distance(parcel_area) <= EXAMPLE_RADIUS_M
        example_features = [
            parcel_feature(row, parcel_color) for row in parcel.itertuples()
        ]
        example_features += [
            restriction_feature(row, styles) for row in wgs84[nearby].itertuples()
        ]
    example = feature_collection(f"parcel_{EXAMPLE_PARCEL}_example", example_features)
    return {
        "hesse_restrictions": everything,
        "synthetic_wfs_demonstrator": synthetic,
        f"parcel_{EXAMPLE_PARCEL}_example": example,
    }


def write_collections(collections: dict[str, dict[str, Any]], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, collection in collections.items():
        path = directory / f"{name}.geojson"
        path.write_text(
            json.dumps(
                collection, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            )
            + "\n",
            encoding="utf-8",
        )
        logger.info(
            "wrote %s (%d features, %d bytes)",
            path,
            len(collection["features"]),
            path.stat().st_size,
        )


def update_readme_blocks(
    readme: Path, collections: dict[str, dict[str, Any]], block_names: list[str]
) -> None:
    text = readme.read_text(encoding="utf-8")
    for name in block_names:
        pattern = re.compile(
            rf"(<!-- github-map:{name} -->\n)```geojson\n.*?\n```(\n<!-- /github-map:{name} -->)",
            re.DOTALL,
        )
        body = json.dumps(
            collections[name],
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        text, replaced = pattern.subn(
            lambda match, b=body: (
                f"{match.group(1)}```geojson\n{b}\n```{match.group(2)}"
            ),
            text,
        )
        if replaced:
            logger.info("updated README map block %s", name)
        else:
            logger.warning("README has no map block markers for %s", name)
    readme.write_text(text, encoding="utf-8")
