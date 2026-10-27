import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime

import requests

from geohazard.config.data_sources import DataSource, LayerConfig
from geohazard.config.settings import HttpSettings
from geohazard.scrapers.base import (
    DATA_CURRENCY_NOT_STATED,
    FetchedPage,
    LayerNotFoundError,
    ScraperError,
    SourceMetadata,
    get,
    sha256_hex,
)

logger = logging.getLogger(__name__)

TARGET_EPSG = 25832


class ArcgisRestScraper:
    def __init__(self, source: DataSource, session: requests.Session, http: HttpSettings):
        self.source = source
        self.session = session
        self.http = http
        self.max_record_count: dict[str, int] = {}

    def _get_json(self, url: str, params: dict[str, str | int]) -> tuple[dict, bytes]:
        response = get(self.session, url, {**params, "f": "json"}, self.http.timeout_seconds)
        payload = response.json()
        if "error" in payload:
            raise ScraperError(f"ArcGIS error from {response.url}: {payload['error']}")
        return payload, response.content

    def discover(self) -> SourceMetadata:
        service, service_raw = self._get_json(self.source.url, {})
        offered = {str(layer["id"]): layer["name"] for layer in service.get("layers", [])}
        missing = [layer.name for layer in self.source.layers if layer.name not in offered]
        if missing:
            raise LayerNotFoundError(f"layers {missing} not offered by {self.source.url}")

        raw_documents = [service_raw]
        schema_fields: dict[str, list[str]] = {}
        currencies: list[str] = []
        for layer in self.source.layers:
            info, info_raw = self._get_json(f"{self.source.url}/{layer.name}", {})
            raw_documents.append(info_raw)
            fields = [f["name"] for f in info.get("fields") or []]
            if layer.id_field not in fields:
                raise ScraperError(f"id field {layer.id_field!r} missing from layer {layer.name} fields {fields}")
            schema_fields[layer.name] = fields
            self.max_record_count[layer.name] = int(info.get("maxRecordCount") or self.http.page_size)
            last_edit = (info.get("editingInfo") or {}).get("lastEditDate")
            if last_edit:
                currencies.append(datetime.fromtimestamp(last_edit / 1000, UTC).isoformat())
            logger.info("layer %s (%s) geometry=%s fields=%s", layer.name, offered[layer.name], info.get("geometryType"), fields)

        copyright_text = service.get("copyrightText") or ""
        return SourceMetadata(
            source_url=self.source.url,
            layer_names=[layer.name for layer in self.source.layers],
            access_constraints="; ".join(filter(None, [self.source.access_constraints, copyright_text])),
            fees=None,
            data_currency=f"lastEditDate {max(currencies)}" if currencies else DATA_CURRENCY_NOT_STATED,
            capabilities_hash=sha256_hex(*raw_documents),
            schema_fields=schema_fields,
        )

    def fetch_layer(self, layer: LayerConfig) -> Iterator[FetchedPage]:
        page_size = min(self.http.page_size, self.max_record_count.get(layer.name, self.http.page_size))
        offset = 0
        url = f"{self.source.url}/{layer.name}/query"
        while True:
            params = {
                "where": "1=1",
                "outFields": "*",
                "outSR": TARGET_EPSG,
                "orderByFields": layer.id_field,
                "resultOffset": offset,
                "resultRecordCount": page_size,
                "f": "geojson",
            }
            response = get(self.session, url, params, self.http.timeout_seconds)
            payload = json.loads(response.content)
            if "error" in payload:
                raise ScraperError(f"ArcGIS error from {response.url}: {payload['error']}")
            count = len(payload.get("features") or [])
            logger.info("fetched layer %s offset=%d features=%d", layer.name, offset, count)
            yield FetchedPage(layer.name, response.url, response.content, "application/geo+json", count)
            exceeded = payload.get("exceededTransferLimit") or (payload.get("properties") or {}).get("exceededTransferLimit")
            if count == 0 or not exceeded:
                return
            offset += count
