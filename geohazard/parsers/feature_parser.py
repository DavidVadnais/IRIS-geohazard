import io
import json
import logging
from collections.abc import Iterable

import geopandas as gpd
import pandas as pd
import pyogrio
from pyproj import CRS
from shapely.geometry import shape

from geohazard.scrapers.base import FetchedPage

logger = logging.getLogger(__name__)

FEATURE_ID_COLUMN = "_feature_id"
GEOMETRY_ERROR_COLUMN = "_geometry_parse_error"


class ParserError(ValueError):
    pass


def _geojson_crs(collection: dict) -> CRS | None:
    name = ((collection.get("crs") or {}).get("properties") or {}).get("name")
    return CRS.from_user_input(name) if name else None


def parse_geojson(content: bytes) -> gpd.GeoDataFrame:
    collection = json.loads(content)
    properties, geometries = [], []
    for item in collection.get("features") or []:
        row = dict(item.get("properties") or {})
        if "id" in item:
            row.setdefault(FEATURE_ID_COLUMN, item["id"])
        geometry = item.get("geometry")
        try:
            geometries.append(shape(geometry) if geometry else None)
        except (ValueError, TypeError, AttributeError) as exc:
            geometries.append(None)
            row[GEOMETRY_ERROR_COLUMN] = str(exc)
            logger.warning(
                "unparseable geometry for feature %s: %s", item.get("id"), exc
            )
        properties.append(row)
    return gpd.GeoDataFrame(
        properties, geometry=gpd.GeoSeries(geometries), crs=_geojson_crs(collection)
    )


def parse_gml(content: bytes) -> gpd.GeoDataFrame:
    return pyogrio.read_dataframe(io.BytesIO(content))


def parse_page(page: FetchedPage) -> gpd.GeoDataFrame:
    if "json" in page.content_type:
        return parse_geojson(page.content)
    return parse_gml(page.content)


def parse_pages(pages: Iterable[FetchedPage]) -> gpd.GeoDataFrame:
    frames = [parse_page(page) for page in pages]
    frames = [frame for frame in frames if len(frame)] or frames[:1]
    if not frames:
        return gpd.GeoDataFrame(geometry=gpd.GeoSeries([]))
    crs_values = {
        frame.crs.to_string() if frame.crs is not None else None for frame in frames
    }
    if len(crs_values) > 1:
        raise ParserError(f"pages of one layer declared different CRS: {crs_values}")
    return gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True), geometry="geometry", crs=frames[0].crs
    )
