from dataclasses import dataclass, field
from enum import StrEnum


class SourceKind(StrEnum):
    WFS = "wfs"
    ARCGIS_REST = "arcgis_rest"


class HarvestMode(StrEnum):
    LIVE = "live"
    FIXTURE = "fixture"


class RestrictionCategory(StrEnum):
    MINE_RIGHT = "mine_right"
    SHAFT = "shaft"
    CAVITY = "cavity"
    CONTAMINATED_LAND = "contaminated_land"
    SINKHOLE_SUBSIDENCE = "sinkhole_subsidence"
    EXTRACTION_SITE = "extraction_site"


HLNUG_LICENSE_ASSUMED = (
    "CC-BY-4.0 (assumed, per HLNUG INSPIRE Nutzungsbedingungen Stand Mai 2025)"
)
HLNUG_ATTRIBUTION = (
    "© Hessisches Landesamt für Naturschutz, Umwelt und Geologie (HLNUG)"
)
HLNUG_ACCESS_CONSTRAINTS = "noLimitations; keine rechtsverbindliche Auskunft"

HESSE_BBOX_25832 = (400_000.0, 5_460_000.0, 600_000.0, 5_740_000.0)

MISSING_CLASSIFICATION_TOKENS = frozenset(
    {"", "unbekannt", "unknown", "k.a.", "k. a.", "n.b.", "n. b.", "-", "null", "none"}
)


@dataclass(frozen=True)
class LayerConfig:
    name: str
    id_field: str
    category_field: str | None = None
    category_map: dict[str, RestrictionCategory] = field(default_factory=dict)
    fixed_category: RestrictionCategory | None = None
    type_field: str | None = None
    status_field: str | None = None
    classification_field: str | None = None
    allowed_geometry_types: frozenset[str] = frozenset(
        {
            "Point",
            "MultiPoint",
            "LineString",
            "MultiLineString",
            "Polygon",
            "MultiPolygon",
        }
    )


@dataclass(frozen=True)
class DataSource:
    key: str
    source: str
    kind: SourceKind
    mode: HarvestMode
    url: str
    layers: tuple[LayerConfig, ...]
    license: str
    attribution: str
    access_constraints: str
    wfs_version: str = "2.0.0"
    output_format: str = "application/json"
    classification_vocabulary: frozenset[str] = frozenset()
    status_map: dict[str, str] = field(default_factory=dict)
    notes: str = ""


DEMO_CATEGORY_MAP = {
    "bergbauberechtigung": RestrictionCategory.MINE_RIGHT,
    "bergwerkseigentum": RestrictionCategory.MINE_RIGHT,
    "bewilligung": RestrictionCategory.MINE_RIGHT,
    "schacht": RestrictionCategory.SHAFT,
    "stollen": RestrictionCategory.SHAFT,
    "tagesoeffnung": RestrictionCategory.SHAFT,
    "hohlraum": RestrictionCategory.CAVITY,
    "kaverne": RestrictionCategory.CAVITY,
    "grubenbau": RestrictionCategory.CAVITY,
    "altflaeche": RestrictionCategory.CONTAMINATED_LAND,
    "altablagerung": RestrictionCategory.CONTAMINATED_LAND,
    "altstandort": RestrictionCategory.CONTAMINATED_LAND,
    "altlast": RestrictionCategory.CONTAMINATED_LAND,
}

DEMO_STATUS_MAP = {
    "aktiv": "active",
    "in betrieb": "active",
    "stillgelegt": "abandoned",
    "erloschen": "expired",
    "verfuellt": "backfilled",
    "saniert": "remediated",
    "in bearbeitung": "under_investigation",
    "altlastverdaechtig": "suspected",
}

DEMO_CLASSIFICATION_VOCABULARY = frozenset(
    {"hoch", "mittel", "gering", "keine gefaehrdung bekannt"}
)

DATA_SOURCES: dict[str, DataSource] = {
    "hlnug_fixture_wfs": DataSource(
        key="hlnug_fixture_wfs",
        source="hlnug_demo",
        kind=SourceKind.WFS,
        mode=HarvestMode.FIXTURE,
        url="https://fixture.invalid/hlnug/wfs",
        layers=(
            LayerConfig(
                name="hlnug_demo:he_bergbau_kavernen",
                id_field="OBJEKT_ID",
                category_field="OBJEKTART",
                category_map=DEMO_CATEGORY_MAP,
                type_field="OBJEKTART",
                status_field="STATUS",
                classification_field="GEFAEHRDUNGSKLASSE",
            ),
        ),
        license="synthetic fixture data, no license",
        attribution="Synthetic test data shaped like a plausible HLNUG schema; not real HLNUG data",
        access_constraints="none (synthetic)",
        classification_vocabulary=DEMO_CLASSIFICATION_VOCABULARY,
        status_map=DEMO_STATUS_MAP,
        notes="No public WFS exists for these categories in Hesse; see docs/source_discovery.md",
    ),
    "hlnug_geogefahren_erdfaelle": DataSource(
        key="hlnug_geogefahren_erdfaelle",
        source="hlnug:geologie/geogefahren/20",
        kind=SourceKind.ARCGIS_REST,
        mode=HarvestMode.LIVE,
        url="https://geodienste-umwelt.hessen.de/arcgis/rest/services/geologie/geogefahren/MapServer",
        layers=(
            LayerConfig(
                name="20",
                id_field="OBJECTID",
                fixed_category=RestrictionCategory.SINKHOLE_SUBSIDENCE,
                allowed_geometry_types=frozenset({"Point", "MultiPoint"}),
            ),
        ),
        license=HLNUG_LICENSE_ASSUMED,
        attribution=HLNUG_ATTRIBUTION,
        access_constraints=HLNUG_ACCESS_CONSTRAINTS,
        notes="Erdfälle und Senkungsmulden; natural geohazard, not a mining category",
    ),
    "hlnug_rohstoffe_abbaustellen": DataSource(
        key="hlnug_rohstoffe_abbaustellen",
        source="hlnug:geologie/rohstoffe/1",
        kind=SourceKind.ARCGIS_REST,
        mode=HarvestMode.LIVE,
        url="https://geodienste-umwelt.hessen.de/arcgis/rest/services/geologie/rohstoffe/MapServer",
        layers=(
            LayerConfig(
                name="1",
                id_field="OBJECTID",
                fixed_category=RestrictionCategory.EXTRACTION_SITE,
                type_field="ABBAUART",
                status_field="GEWINNUNGS",
                allowed_geometry_types=frozenset({"Point", "MultiPoint"}),
            ),
        ),
        license=HLNUG_LICENSE_ASSUMED,
        attribution=HLNUG_ATTRIBUTION,
        access_constraints=HLNUG_ACCESS_CONSTRAINTS,
        notes="Abbaustellen; surface extraction sites with ABBAURECHT, not mine rights or shafts",
    ),
}


def get_data_source(key: str) -> DataSource:
    try:
        return DATA_SOURCES[key]
    except KeyError as exc:
        raise ValueError(
            f"unknown data source {key!r}; known: {sorted(DATA_SOURCES)}"
        ) from exc
