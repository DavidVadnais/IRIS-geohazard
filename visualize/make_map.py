import argparse
import html
import json
import logging
import sys
from pathlib import Path

import folium
import geopandas as gpd
import requests
from folium.plugins import Fullscreen
from shapely import wkt
from shapely.errors import GEOSException
from sqlalchemy import create_engine

from geohazard.config.settings import get_settings
from geohazard.logging_config import configure_logging

logger = logging.getLogger("visualize.make_map")

HERE = Path(__file__).resolve().parent
DEFAULT_OUTPUT = HERE / "output" / "hesse_restrictions_map.html"
DEFAULT_STATES_CACHE = HERE / "data" / "vg2500_lan.geojson"
DEFAULT_PARCELS = HERE / "example_parcels.geojson"
DEFAULT_STATE = "Hessen"

STATES_WFS_URL = "https://sgx.geodatenzentrum.de/wfs_vg2500"
STATES_WFS_PARAMS = {
    "service": "WFS",
    "version": "2.0.0",
    "request": "GetFeature",
    "typeNames": "vg2500:vg2500_lan",
    "outputFormat": "application/json",
    "srsName": "EPSG:4326",
}
LAND_GEOFACTOR = 9
STATES_ATTRIBUTION = "Verwaltungsgebiete: © GeoBasis-DE / BKG (VG2500), dl-de/by-2-0"
HLNUG_ATTRIBUTION = (
    "Restriktionen: © HLNUG (CC BY 4.0, assumed) and synthetic IRIS fixture data"
)

TOPPLUS_URL = "https://sgx.geodatenzentrum.de/wmts_topplus_open/tile/1.0.0/{variant}/default/WEBMERCATOR/{{z}}/{{y}}/{{x}}.png"
TOPPLUS_ATTRIBUTION = (
    '© <a href="https://www.bkg.bund.de">BKG</a> (2026) '
    '<a href="https://www.govdata.de/dl-de/by-2-0">dl-de/by-2-0</a>, '
    '<a href="https://sg.geodatenzentrum.de/web_public/Datenquellen_TopPlus_Open.pdf">Datenquellen</a>'
)

GERMANY_BOUNDS = [[47.27, 5.87], [55.06, 15.04]]

CATEGORY_STYLES = {
    "mine_right": ("Mine rights (synthetic)", "#7b3294"),
    "shaft": ("Shafts / adits (synthetic)", "#d7191c"),
    "cavity": ("Cavities / caverns (synthetic)", "#fdae61"),
    "contaminated_land": ("Contaminated land (synthetic)", "#8c510a"),
    "sinkhole_subsidence": ("Sinkholes & subsidence (HLNUG live)", "#2b83ba"),
    "extraction_site": ("Extraction sites (HLNUG live)", "#1a9641"),
}
REJECTED_STYLE = ("Rejected records with geometry", "#000000")
PARCELS_STYLE = ("ALKIS example parcels (HVBG, sql/examples.sql)", "#c51b7d")
PARCELS_ATTRIBUTION = "Flurstücke: HVBG INSPIRE-WFS ALKIS, dl-de/zero-2.0"

PARCEL_POPUP_FIELDS = [
    ("flurstueck", "Flurstück"),
    ("national_cadastral_reference", "Cadastral reference"),
    ("official_area_m2", "Official area (m²)"),
    ("delivered_as", "Delivered as"),
    ("sql_variable", "SQL variable"),
    ("license", "License"),
    ("fetched", "Fetched"),
    ("source_link", "Source"),
]

RESTRICTIONS_SQL = """
SELECT source, source_feature_id, restriction_category, hazard_type, status, status_raw,
       hazard_classification, classification_status, layer_name, source_crs, geom_was_repaired,
       license, source_url, to_char(fetched_at, 'YYYY-MM-DD HH24:MI') AS fetched_at,
       ST_Transform(geom, 4326) AS geom
FROM iris_staging.mining_restrictions
"""

REJECTED_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (source) run_id FROM iris_staging.harvest_runs ORDER BY source, started_at DESC
)
SELECT j.source, j.source_feature_id, j.reason_code, j.reason_detail, j.layer_name, j.raw_geometry_wkt,
       h.mode
FROM iris_staging.mining_restrictions_rejected j
JOIN latest l ON l.run_id = j.run_id
JOIN iris_staging.harvest_runs h ON h.run_id = j.run_id
WHERE j.raw_geometry_wkt IS NOT NULL
"""

POPUP_FIELDS = [
    ("restriction_category", "Category"),
    ("source_feature_id", "Feature ID"),
    ("source", "Source"),
    ("hazard_type", "Type"),
    ("status_display", "Status"),
    ("classification_display", "Hazard classification"),
    ("geom_was_repaired", "Geometry repaired"),
    ("source_crs", "Source CRS"),
    ("fetched_at", "Fetched"),
    ("license", "License"),
    ("source_link", "Source"),
]


PREBUILT_HTML_FIELDS = frozenset(
    {"status_display", "classification_display", "source_link"}
)


def escape_columns(frame: gpd.GeoDataFrame, columns) -> None:
    for column in columns:
        frame[column] = [
            html.escape(str(value)) if value is not None else ""
            for value in frame[column]
        ]


def load_states(cache: Path, refresh: bool) -> gpd.GeoDataFrame:
    if refresh or not cache.is_file():
        logger.info("downloading state boundaries from %s", STATES_WFS_URL)
        response = requests.get(STATES_WFS_URL, params=STATES_WFS_PARAMS, timeout=60)
        response.raise_for_status()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(response.content)
    states = gpd.read_file(cache)
    if states.crs is None or states.crs.to_epsg() != 4326:
        states = (
            states.set_crs(4326, allow_override=True)
            if states.crs is None
            else states.to_crs(4326)
        )
    land = states[states["gf"] == LAND_GEOFACTOR]
    return land.dissolve(by="gen", as_index=False)[["gen", "bez", "nuts", "geometry"]]


def classification_display(row) -> str:
    if row.classification_status == "missing":
        return "<b>MISSING</b> (not published, not safe)"
    if row.classification_status == "unrecognised":
        return f"{html.escape(row.hazard_classification)} (unrecognised value)"
    return html.escape(row.hazard_classification)


def load_restrictions(engine) -> gpd.GeoDataFrame:
    frame = gpd.read_postgis(RESTRICTIONS_SQL, engine, geom_col="geom")
    if frame.empty:
        return frame
    frame["classification_display"] = frame.apply(classification_display, axis=1)
    frame["status_display"] = [
        html.escape(f"{s} ({raw})" if s and raw else (s or raw or "unknown"))
        for s, raw in zip(frame["status"], frame["status_raw"], strict=True)
    ]
    frame["source_link"] = [
        f'<a href="{html.escape(url)}" target="_blank">open</a>'
        for url in frame["source_url"]
    ]
    frame["geom_was_repaired"] = frame["geom_was_repaired"].map(
        {True: "yes", False: "no"}
    )
    escape_columns(
        frame, [field for field, _ in POPUP_FIELDS if field not in PREBUILT_HTML_FIELDS]
    )
    return frame


def load_rejected(engine) -> gpd.GeoDataFrame:
    rows = []
    with engine.connect() as connection:
        result = connection.exec_driver_sql(REJECTED_SQL)
        columns = list(result.keys())
        for record in result:
            row = dict(zip(columns, record, strict=True))
            try:
                geometry = wkt.loads(row.pop("raw_geometry_wkt"))
            except GEOSException:
                continue
            if geometry.is_empty:
                continue
            row["geometry"] = geometry
            rows.append(row)
    if not rows:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs=4326)
    frame = gpd.GeoDataFrame(rows, geometry="geometry", crs=25832).to_crs(4326)
    frame["geometry"] = frame.geometry.make_valid()
    escape_columns(
        frame,
        [
            "source_feature_id",
            "source",
            "reason_code",
            "reason_detail",
            "mode",
            "layer_name",
        ],
    )
    return frame


def load_parcels(path: Path) -> gpd.GeoDataFrame:
    if not path.is_file():
        logger.warning("example parcels file %s not found; parcel layer skipped", path)
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs=4326)
    parcels = gpd.read_file(path).to_crs(4326)
    parcels["source_link"] = [
        f'<a href="{html.escape(url)}?SERVICE=WFS&amp;REQUEST=GetCapabilities&amp;VERSION=2.0.0" '
        'target="_blank">GetCapabilities</a>'
        for url in parcels["source_service"]
    ]
    escape_columns(
        parcels, [field for field, _ in PARCEL_POPUP_FIELDS if field != "source_link"]
    )
    return parcels


def add_states(fmap: folium.Map, states: gpd.GeoDataFrame, focus_state: str) -> None:
    def style(feature):
        focused = feature["properties"]["gen"] == focus_state
        return {
            "fillColor": "#ffd54f" if focused else "#e0e0e0",
            "color": "#e65100" if focused else "#757575",
            "weight": 3 if focused else 1,
            "fillOpacity": 0.25 if focused else 0.1,
        }

    folium.GeoJson(
        states,
        name="German states (click to zoom)",
        style_function=style,
        highlight_function=lambda _: {"weight": 3, "fillOpacity": 0.35},
        tooltip=folium.GeoJsonTooltip(fields=["gen", "bez"], aliases=["State", "Type"]),
        zoom_on_click=True,
    ).add_to(fmap)


def add_category_layers(fmap: folium.Map, restrictions: gpd.GeoDataFrame) -> None:
    for category, (label, color) in CATEGORY_STYLES.items():
        subset = restrictions[restrictions["restriction_category"] == category]
        if subset.empty:
            continue
        group = folium.FeatureGroup(name=f"{label} ({len(subset)})", show=True)
        folium.GeoJson(
            subset.drop(
                columns=["source_url", "status", "status_raw", "hazard_classification"]
            ),
            style_function=lambda _, c=color: {
                "color": c,
                "fillColor": c,
                "weight": 2,
                "fillOpacity": 0.4,
            },
            marker=folium.CircleMarker(radius=4, fill=True, fill_opacity=0.8, weight=1),
            tooltip=folium.GeoJsonTooltip(
                fields=["source_feature_id", "restriction_category"],
                aliases=["ID", "Category"],
            ),
            popup=folium.GeoJsonPopup(
                fields=[f for f, _ in POPUP_FIELDS],
                aliases=[a for _, a in POPUP_FIELDS],
                localize=False,
                labels=True,
            ),
        ).add_to(group)
        group.add_to(fmap)


def add_rejected_layer(fmap: folium.Map, rejected: gpd.GeoDataFrame) -> None:
    if rejected.empty:
        return
    label, color = REJECTED_STYLE
    group = folium.FeatureGroup(name=f"{label} ({len(rejected)})", show=False)
    folium.GeoJson(
        rejected,
        style_function=lambda _: {
            "color": color,
            "weight": 2,
            "dashArray": "4 4",
            "fillOpacity": 0.1,
        },
        marker=folium.CircleMarker(radius=6, fill=False, weight=2, dash_array="3 3"),
        tooltip=folium.GeoJsonTooltip(
            fields=["source_feature_id", "reason_code"], aliases=["ID", "Rejected"]
        ),
        popup=folium.GeoJsonPopup(
            fields=[
                "source_feature_id",
                "source",
                "reason_code",
                "reason_detail",
                "mode",
            ],
            aliases=["Feature ID", "Source", "Reason", "Detail", "Mode"],
        ),
    ).add_to(group)
    group.add_to(fmap)


def add_parcels_layer(fmap: folium.Map, parcels: gpd.GeoDataFrame) -> None:
    if parcels.empty:
        return
    label, color = PARCELS_STYLE
    group = folium.FeatureGroup(name=f"{label} ({len(parcels)})", show=False)
    folium.GeoJson(
        parcels[[field for field, _ in PARCEL_POPUP_FIELDS] + ["geometry"]],
        style_function=lambda _: {
            "color": color,
            "weight": 3,
            "fillOpacity": 0.05,
        },
        tooltip=folium.GeoJsonTooltip(fields=["flurstueck"], aliases=["Flurstück"]),
        popup=folium.GeoJsonPopup(
            fields=[f for f, _ in PARCEL_POPUP_FIELDS],
            aliases=[a for _, a in PARCEL_POPUP_FIELDS],
            localize=False,
            labels=True,
        ),
    ).add_to(group)
    group.add_to(fmap)


def add_legend(fmap: folium.Map) -> None:
    items = "".join(
        f'<div><span style="display:inline-block;width:12px;height:12px;background:{color};'
        f'margin-right:6px;border-radius:50%"></span>{html.escape(label)}</div>'
        for label, color in [*CATEGORY_STYLES.values(), REJECTED_STYLE]
    )
    parcel_label, parcel_color = PARCELS_STYLE
    items += (
        f'<div><span style="display:inline-block;width:10px;height:10px;border:2px solid {parcel_color};'
        f'margin-right:6px"></span>{html.escape(parcel_label)}</div>'
    )
    legend = (
        '<div style="position:fixed;bottom:24px;left:12px;z-index:9999;background:white;padding:8px 10px;'
        'border:1px solid #999;border-radius:4px;font:12px sans-serif;max-width:260px">'
        "<b>IRIS TASK 090: Hesse restrictions</b>"
        f"{items}"
        '<div style="margin-top:6px;color:#555">Synthetic layers are demonstrator data, not real HLNUG records. '
        "Click a state to zoom, a feature for details.</div>"
        f'<div style="margin-top:6px;color:#777;font-size:10px">{html.escape(STATES_ATTRIBUTION)}<br>'
        f"{html.escape(HLNUG_ATTRIBUTION)}<br>{html.escape(PARCELS_ATTRIBUTION)}</div></div>"
    )
    fmap.get_root().html.add_child(folium.Element(legend))


def build_map(states, restrictions, rejected, parcels, focus_state: str) -> folium.Map:
    fmap = folium.Map(tiles=None, prefer_canvas=True, control_scale=True)
    for variant, name in (
        ("web", "TopPlusOpen colour (BKG)"),
        ("web_grau", "TopPlusOpen grey (BKG)"),
    ):
        folium.TileLayer(
            TOPPLUS_URL.format(variant=variant),
            name=name,
            attr=TOPPLUS_ATTRIBUTION,
            max_zoom=18,
        ).add_to(fmap)
    add_states(fmap, states, focus_state)
    if not restrictions.empty:
        add_category_layers(fmap, restrictions)
    add_rejected_layer(fmap, rejected)
    add_parcels_layer(fmap, parcels)
    folium.LayerControl(collapsed=False).add_to(fmap)
    Fullscreen().add_to(fmap)
    add_legend(fmap)
    focus = states[states["gen"] == focus_state]
    if focus.empty:
        fmap.fit_bounds(GERMANY_BOUNDS)
    else:
        min_x, min_y, max_x, max_y = focus.total_bounds
        fmap.fit_bounds([[min_y, min_x], [max_y, max_x]])
    return fmap


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render iris_staging restrictions on a map of German states"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--state",
        default=DEFAULT_STATE,
        help="state to highlight and zoom to (VG2500 'gen' name)",
    )
    parser.add_argument("--states-cache", type=Path, default=DEFAULT_STATES_CACHE)
    parser.add_argument(
        "--parcels",
        type=Path,
        default=DEFAULT_PARCELS,
        help="GeoJSON of example parcels to draw as an optional layer",
    )
    parser.add_argument(
        "--refresh-states",
        action="store_true",
        help="re-download state boundaries from BKG",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings.database.sqlalchemy_url())

    states = load_states(args.states_cache, args.refresh_states)
    if args.state not in set(states["gen"]):
        logger.error(
            "unknown state %r; choose one of %s", args.state, sorted(states["gen"])
        )
        return 1
    restrictions = load_restrictions(engine)
    rejected = load_rejected(engine)
    parcels = load_parcels(args.parcels)
    logger.info(
        "restrictions=%d by category=%s rejected_with_geometry=%d parcels=%d",
        len(restrictions),
        json.dumps(restrictions["restriction_category"].value_counts().to_dict())
        if len(restrictions)
        else "{}",
        len(rejected),
        len(parcels),
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    build_map(states, restrictions, rejected, parcels, args.state).save(args.output)
    logger.info("map written to %s", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
