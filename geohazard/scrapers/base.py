import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from geohazard.config.data_sources import LayerConfig
from geohazard.config.settings import HttpSettings

DATA_CURRENCY_NOT_STATED = "not stated by service"


class ScraperError(RuntimeError):
    pass


class LayerNotFoundError(ScraperError):
    pass


@dataclass(frozen=True)
class SourceMetadata:
    source_url: str
    layer_names: list[str]
    access_constraints: str | None
    fees: str | None
    data_currency: str
    capabilities_hash: str
    schema_fields: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class FetchedPage:
    layer_name: str
    url: str
    content: bytes
    content_type: str
    feature_count: int


class Scraper(Protocol):
    def discover(self) -> SourceMetadata: ...

    def fetch_layer(self, layer: LayerConfig) -> Iterator[FetchedPage]: ...


def sha256_hex(*parts: bytes) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part)
    return digest.hexdigest()


def build_session(http: HttpSettings) -> requests.Session:
    retry = Retry(
        total=http.max_retries,
        backoff_factor=http.backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    session = requests.Session()
    session.headers["User-Agent"] = http.user_agent
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get(session: requests.Session, url: str, params: dict[str, str | int], timeout: float) -> requests.Response:
    response = session.get(url, params=params, timeout=timeout)
    if response.status_code != 200:
        raise ScraperError(f"GET {response.url} returned HTTP {response.status_code}")
    return response
