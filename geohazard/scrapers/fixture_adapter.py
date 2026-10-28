from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests
from requests.adapters import BaseAdapter

CONTENT_TYPES = {
    ".xml": "application/xml",
    ".xsd": "application/xml",
    ".json": "application/json",
    ".gml": "application/gml+xml; version=3.2",
}


def fixture_filename(params: dict[str, str]) -> str | None:
    request = params.get("request", "").lower()
    if request == "getcapabilities":
        return "capabilities.xml"
    if request == "describefeaturetype":
        return "describefeaturetype.xsd"
    if request == "getfeature":
        extension = "json" if "json" in params.get("outputformat", "") else "gml"
        return f"getfeature_{params.get('startindex', '0')}.{extension}"
    return None


class FixtureAdapter(BaseAdapter):
    def __init__(self, directory: Path):
        super().__init__()
        self.directory = directory

    def send(self, request: requests.PreparedRequest, **kwargs) -> requests.Response:
        query = parse_qs(urlsplit(request.url).query)
        params = {key.lower(): values[0] for key, values in query.items()}
        name = fixture_filename(params)
        path = self.directory / name if name else None

        response = requests.Response()
        response.url = request.url
        response.request = request
        if path is None or not path.is_file():
            response.status_code = 404
            response._content = f"no fixture for {params}".encode()
            return response
        response.status_code = 200
        response._content = path.read_bytes()
        response.headers["Content-Type"] = CONTENT_TYPES.get(
            path.suffix, "application/octet-stream"
        )
        return response

    def close(self) -> None:
        pass


def mount_fixture(session: requests.Session, base_url: str, directory: Path) -> None:
    if not directory.is_dir():
        raise FileNotFoundError(f"fixture directory {directory} does not exist")
    session.mount(base_url, FixtureAdapter(directory))
