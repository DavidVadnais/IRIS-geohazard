import json
import logging
from collections.abc import Iterator

import requests
from lxml import etree
from owslib.wfs import WebFeatureService

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

XSD_NS = "http://www.w3.org/2001/XMLSchema"
GML_OUTPUT_FORMAT = "application/gml+xml; version=3.2"


class WfsScraper:
    def __init__(self, source: DataSource, session: requests.Session, http: HttpSettings):
        self.source = source
        self.session = session
        self.http = http
        self.output_format = source.output_format

    def _get(self, params: dict[str, str | int]) -> requests.Response:
        base = {"service": "WFS", "version": self.source.wfs_version}
        return get(self.session, self.source.url, {**base, **params}, self.http.timeout_seconds)

    def discover(self) -> SourceMetadata:
        capabilities = self._get({"request": "GetCapabilities"}).content
        service = WebFeatureService(self.source.url, version=self.source.wfs_version, xml=capabilities)
        offered = set(service.contents)
        missing = [layer.name for layer in self.source.layers if layer.name not in offered]
        if missing:
            raise LayerNotFoundError(f"layers {missing} not offered by {self.source.url}; offered: {sorted(offered)}")
        self.output_format = self._choose_output_format(service)

        schema_fields: dict[str, list[str]] = {}
        schema_documents: list[bytes] = []
        for layer in self.source.layers:
            schema = self._get({"request": "DescribeFeatureType", "typeNames": layer.name}).content
            schema_documents.append(schema)
            fields = describe_feature_type_fields(schema)
            schema_fields[layer.name] = fields
            self._check_schema(layer, fields)
            logger.info(
                "layer %s crs_options=%s fields=%s",
                layer.name,
                [str(crs) for crs in service.contents[layer.name].crsOptions],
                fields,
            )

        identification = service.identification
        return SourceMetadata(
            source_url=self.source.url,
            layer_names=[layer.name for layer in self.source.layers],
            access_constraints=getattr(identification, "accessconstraints", None) or self.source.access_constraints,
            fees=getattr(identification, "fees", None),
            data_currency=DATA_CURRENCY_NOT_STATED,
            capabilities_hash=sha256_hex(capabilities, *schema_documents),
            schema_fields=schema_fields,
        )

    def _choose_output_format(self, service: WebFeatureService) -> str:
        try:
            formats = service.getOperationByName("GetFeature").parameters["outputFormat"]["values"]
        except (KeyError, AttributeError):
            formats = []
        if self.source.output_format in formats:
            return self.source.output_format
        logger.warning("%s not offered (offered: %s); falling back to GML 3.2", self.source.output_format, formats)
        return GML_OUTPUT_FORMAT

    def _check_schema(self, layer: LayerConfig, fields: list[str]) -> None:
        if layer.id_field not in fields:
            raise ScraperError(f"id field {layer.id_field!r} missing from {layer.name} schema {fields}")
        for optional in (layer.category_field, layer.status_field, layer.classification_field, layer.type_field):
            if optional and optional not in fields:
                logger.warning("field %r missing from %s schema; values will be treated as absent", optional, layer.name)

    def fetch_layer(self, layer: LayerConfig) -> Iterator[FetchedPage]:
        start_index = 0
        page_size = self.http.page_size
        while True:
            response = self._get(
                {
                    "request": "GetFeature",
                    "typeNames": layer.name,
                    "outputFormat": self.output_format,
                    "count": page_size,
                    "startIndex": start_index,
                }
            )
            count = page_feature_count(response.content, response.headers.get("Content-Type", ""))
            logger.info("fetched %s startIndex=%d features=%d", layer.name, start_index, count)
            yield FetchedPage(layer.name, response.url, response.content, response.headers.get("Content-Type", ""), count)
            if count < page_size:
                return
            start_index += count


def describe_feature_type_fields(schema: bytes) -> list[str]:
    root = etree.fromstring(schema)
    elements = root.iterfind(f".//{{{XSD_NS}}}complexType//{{{XSD_NS}}}element")
    return [element.get("name") for element in elements if element.get("name")]


def page_feature_count(content: bytes, content_type: str) -> int:
    if "json" in content_type:
        return len(json.loads(content).get("features") or [])
    root = etree.fromstring(content)
    returned = root.get("numberReturned")
    if returned is None:
        raise ScraperError("GML FeatureCollection has no numberReturned attribute; cannot page safely")
    return int(returned)
