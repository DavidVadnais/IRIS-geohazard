import hashlib
import json
import logging
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry.base import BaseGeometry

from geohazard.config.data_sources import (
    HESSE_BBOX_25832,
    MISSING_CLASSIFICATION_TOKENS,
    DataSource,
    LayerConfig,
    RestrictionCategory,
    SourceKind,
)

logger = logging.getLogger(__name__)

TARGET_EPSG = 25832
ACCEPTED_SOURCE_EPSG = frozenset({25832, 31467, 4326, 4258})
PRECISION_METERS = 0.001


@dataclass(frozen=True)
class FetchContext:
    source: DataSource
    layer: LayerConfig
    fetched_at: datetime


@dataclass(frozen=True)
class StagedRecord:
    source: str
    source_feature_id: str
    restriction_category: str
    hazard_type: str | None
    status: str | None
    status_raw: str | None
    hazard_classification: str | None
    classification_status: str
    attributes: dict[str, Any]
    geom: BaseGeometry
    geom_type: str
    geom_was_repaired: bool
    source_crs: str
    content_hash: str
    source_url: str
    layer_name: str
    license: str
    fetched_at: datetime


@dataclass(frozen=True)
class RejectedRecord:
    source: str
    layer_name: str
    source_feature_id: str | None
    reason_code: str
    reason_detail: str
    raw_feature: dict[str, Any]
    raw_geometry_wkt: str | None
    source_url: str
    fetched_at: datetime


@dataclass
class TransformResult:
    staged: list[StagedRecord] = field(default_factory=list)
    rejected: list[RejectedRecord] = field(default_factory=list)


@dataclass
class _Candidate:
    feature_id: str
    category: RestrictionCategory
    attributes: dict[str, Any]
    geometry: BaseGeometry
    raw_wkt: str
    repaired: bool


def fold_text(value: str) -> str:
    replacements = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
    lowered = value.strip().lower()
    for umlaut, ascii_form in replacements.items():
        lowered = lowered.replace(umlaut, ascii_form)
    decomposed = unicodedata.normalize("NFKD", lowered)
    return " ".join(
        "".join(ch for ch in decomposed if not unicodedata.combining(ch)).split()
    )


def json_safe(value: Any) -> Any:
    if value is None or (not isinstance(value, (list, tuple, dict)) and pd.isna(value)):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "item") and callable(value.item):
        return json_safe(value.item())
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def text_or_none(value: Any) -> str | None:
    safe = json_safe(value)
    if safe is None:
        return None
    text = str(safe).strip()
    return text or None


def classify(raw_value: Any, vocabulary: frozenset[str]) -> tuple[str | None, str]:
    text = text_or_none(raw_value)
    if text is None or fold_text(text) in MISSING_CLASSIFICATION_TOKENS:
        return None, "missing"
    if fold_text(text) in vocabulary:
        return text, "published"
    return text, "unrecognised"


def feature_url(source: DataSource, layer: LayerConfig, feature_id: str) -> str:
    if source.kind is SourceKind.WFS:
        params = {
            "service": "WFS",
            "version": source.wfs_version,
            "request": "GetFeature",
            "typeNames": layer.name,
            "resourceId": feature_id,
        }
        return f"{source.url}?{urlencode(params)}"
    params = {
        "where": f"{layer.id_field}={feature_id}",
        "outFields": "*",
        "f": "geojson",
    }
    return f"{source.url}/{layer.name}/query?{urlencode(params)}"


def content_hash(record_fields: dict[str, Any], geometry: BaseGeometry) -> str:
    payload = dict(record_fields)
    payload["geom_wkb"] = shapely.to_wkb(
        shapely.normalize(geometry), hex=True, output_dimension=2
    )
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _resolve_category(
    layer: LayerConfig, attributes: dict[str, Any]
) -> RestrictionCategory | None:
    if layer.fixed_category is not None:
        return layer.fixed_category
    raw = (
        text_or_none(attributes.get(layer.category_field))
        if layer.category_field
        else None
    )
    return layer.category_map.get(fold_text(raw)) if raw else None


def _repair(geometry: BaseGeometry, allowed: frozenset[str]) -> BaseGeometry | None:
    repaired = shapely.make_valid(geometry, method="structure", keep_collapsed=False)
    if repaired.is_empty or not repaired.is_valid or repaired.geom_type not in allowed:
        return None
    return repaired


def _within_hesse(geometry: BaseGeometry) -> bool:
    min_x, min_y, max_x, max_y = geometry.bounds
    hesse_min_x, hesse_min_y, hesse_max_x, hesse_max_y = HESSE_BBOX_25832
    return (
        min_x >= hesse_min_x
        and min_y >= hesse_min_y
        and max_x <= hesse_max_x
        and max_y <= hesse_max_y
    )


class HazardTransformer:
    def __init__(self, context: FetchContext):
        self.context = context
        self.source = context.source
        self.layer = context.layer

    def transform(self, frame: gpd.GeoDataFrame) -> TransformResult:
        result = TransformResult()
        source_epsg = frame.crs.to_epsg() if frame.crs is not None else None
        crs_problem = self._crs_problem(frame, source_epsg)
        seen_ids: set[str] = set()
        candidates: list[_Candidate] = []

        for _, row in frame.iterrows():
            attributes = {
                key: json_safe(value)
                for key, value in row.items()
                if key != frame.geometry.name
            }
            geometry = row[frame.geometry.name]
            feature_id = text_or_none(attributes.get(self.layer.id_field))
            raw_wkt = geometry.wkt if isinstance(geometry, BaseGeometry) else None

            outcome = self._screen_row(
                feature_id, attributes, geometry, raw_wkt, seen_ids, crs_problem
            )
            if isinstance(outcome, _Candidate):
                candidates.append(outcome)
            else:
                reason, detail = outcome
                result.rejected.append(
                    self._rejected(feature_id, reason, detail, attributes, raw_wkt)
                )

        self._finalise(candidates, source_epsg, result)
        return result

    def _screen_row(
        self,
        feature_id: str | None,
        attributes: dict[str, Any],
        geometry: Any,
        raw_wkt: str | None,
        seen_ids: set[str],
        crs_problem: str | None,
    ) -> _Candidate | tuple[str, str]:
        if feature_id is None:
            return (
                "missing_feature_id",
                f"id field {self.layer.id_field!r} is null or empty",
            )
        if feature_id in seen_ids:
            return (
                "duplicate_feature_id",
                "feature id already seen earlier in this fetch; first occurrence kept",
            )
        seen_ids.add(feature_id)
        if crs_problem is not None:
            return ("unknown_crs", crs_problem)
        category = _resolve_category(self.layer, attributes)
        if category is None:
            raw_category = (
                attributes.get(self.layer.category_field)
                if self.layer.category_field
                else None
            )
            return (
                "unmapped_category",
                f"category value {raw_category!r} has no mapping",
            )
        if not isinstance(geometry, BaseGeometry):
            return ("null_geometry", "geometry is null")
        if geometry.is_empty:
            return ("empty_geometry", "geometry is empty")
        if geometry.geom_type not in self.layer.allowed_geometry_types:
            return (
                "unsupported_geometry_type",
                f"geometry type {geometry.geom_type} is not allowed for this layer",
            )
        repaired = False
        if not geometry.is_valid:
            reason = shapely.is_valid_reason(geometry)
            fixed = _repair(geometry, self.layer.allowed_geometry_types)
            if fixed is None:
                return (
                    "invalid_geometry_unrepairable",
                    f"invalid geometry ({reason}); make_valid gave no usable result",
                )
            logger.info(
                "repaired invalid geometry source=%s feature=%s reason=%s",
                self.source.source,
                feature_id,
                reason,
            )
            geometry, repaired = fixed, True
        return _Candidate(feature_id, category, attributes, geometry, raw_wkt, repaired)

    def _crs_problem(
        self, frame: gpd.GeoDataFrame, source_epsg: int | None
    ) -> str | None:
        if frame.crs is None:
            return "response declared no CRS; refusing to assume one"
        if source_epsg not in ACCEPTED_SOURCE_EPSG:
            return f"CRS {frame.crs.to_string()} is not in accepted set {sorted(ACCEPTED_SOURCE_EPSG)}"
        return None

    def _finalise(
        self,
        candidates: list[_Candidate],
        source_epsg: int | None,
        result: TransformResult,
    ) -> None:
        if not candidates:
            return
        projected = gpd.GeoSeries(
            [c.geometry for c in candidates], crs=f"EPSG:{source_epsg}"
        ).to_crs(epsg=TARGET_EPSG)
        source_crs = f"EPSG:{source_epsg}"
        for candidate, geometry in zip(candidates, projected, strict=True):
            if not _within_hesse(geometry):
                detail = f"bounds {tuple(round(b) for b in geometry.bounds)} in EPSG:{TARGET_EPSG} fall outside Hesse"
                result.rejected.append(
                    self._rejected(
                        candidate.feature_id,
                        "out_of_region",
                        detail,
                        candidate.attributes,
                        candidate.raw_wkt,
                    )
                )
                continue
            geometry = shapely.set_precision(geometry, PRECISION_METERS)
            if geometry.is_empty or not geometry.is_valid:
                result.rejected.append(
                    self._rejected(
                        candidate.feature_id,
                        "invalid_geometry_unrepairable",
                        "geometry collapsed or became invalid at millimetre precision",
                        candidate.attributes,
                        candidate.raw_wkt,
                    )
                )
                continue
            result.staged.append(self._staged(candidate, geometry, source_crs))

    def _staged(
        self, candidate: _Candidate, geometry: BaseGeometry, source_crs: str
    ) -> StagedRecord:
        attributes = candidate.attributes
        classification, classification_status = (
            classify(
                attributes.get(self.layer.classification_field),
                self.source.classification_vocabulary,
            )
            if self.layer.classification_field
            else (None, "missing")
        )
        status_raw = (
            text_or_none(attributes.get(self.layer.status_field))
            if self.layer.status_field
            else None
        )
        mapped = {
            "source": self.source.source,
            "source_feature_id": candidate.feature_id,
            "restriction_category": candidate.category.value,
            "hazard_type": text_or_none(attributes.get(self.layer.type_field))
            if self.layer.type_field
            else None,
            "status": self.source.status_map.get(fold_text(status_raw))
            if status_raw
            else None,
            "status_raw": status_raw,
            "hazard_classification": classification,
            "classification_status": classification_status,
            "attributes": attributes,
            "geom_type": geometry.geom_type,
            "geom_was_repaired": candidate.repaired,
            "source_crs": source_crs,
            "source_url": feature_url(self.source, self.layer, candidate.feature_id),
            "layer_name": self.layer.name,
            "license": self.source.license,
        }
        return StagedRecord(
            **mapped,
            geom=geometry,
            content_hash=content_hash(mapped, geometry),
            fetched_at=self.context.fetched_at,
        )

    def _rejected(
        self,
        feature_id: str | None,
        reason: str,
        detail: str,
        attributes: dict[str, Any],
        raw_wkt: str | None,
    ) -> RejectedRecord:
        logger.warning(
            "rejected source=%s layer=%s feature=%s reason=%s detail=%s",
            self.source.source,
            self.layer.name,
            feature_id,
            reason,
            detail,
        )
        source_url = (
            feature_url(self.source, self.layer, feature_id)
            if feature_id is not None
            else self.source.url
        )
        return RejectedRecord(
            source=self.source.source,
            layer_name=self.layer.name,
            source_feature_id=feature_id,
            reason_code=reason,
            reason_detail=detail,
            raw_feature=attributes,
            raw_geometry_wkt=raw_wkt,
            source_url=source_url,
            fetched_at=self.context.fetched_at,
        )
