import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import requests
from sqlalchemy import create_engine

from geohazard.config.data_sources import (
    DataSource,
    HarvestMode,
    SourceKind,
    get_data_source,
)
from geohazard.config.settings import Settings, get_settings
from geohazard.loaders.postgis_loader import LoadCounts, PostgisLoader, RunMetadata
from geohazard.logging_config import configure_logging
from geohazard.parsers.feature_parser import parse_pages
from geohazard.scrapers.arcgis_rest_scraper import ArcgisRestScraper
from geohazard.scrapers.base import Scraper, build_session
from geohazard.scrapers.fixture_adapter import mount_fixture
from geohazard.scrapers.wfs_scraper import WfsScraper
from geohazard.transformers.hazard_transformer import FetchContext, HazardTransformer

logger = logging.getLogger("geohazard.runner")

DEFAULT_FIXTURE_DIR = Path("fixtures/hlnug_demo")


def build_scraper(
    source: DataSource, session: requests.Session, settings: Settings
) -> Scraper:
    if source.kind is SourceKind.WFS:
        return WfsScraper(source, session, settings.http)
    return ArcgisRestScraper(source, session, settings.http)


def config_metadata(source: DataSource) -> RunMetadata:
    return RunMetadata(
        source_key=source.key,
        source=source.source,
        mode=source.mode.value,
        source_url=source.url,
        layer_names=[layer.name for layer in source.layers],
        license=source.license,
        attribution=source.attribution,
        access_constraints=source.access_constraints,
    )


def harvest_source(
    source: DataSource, scraper: Scraper, loader: PostgisLoader
) -> LoadCounts:
    try:
        discovered = scraper.discover()
    except Exception as exc:
        loader.fail_run(
            loader.start_run(config_metadata(source)), f"discovery failed: {exc}"
        )
        raise

    run_id = loader.start_run(
        RunMetadata(
            source_key=source.key,
            source=source.source,
            mode=source.mode.value,
            source_url=discovered.source_url,
            layer_names=discovered.layer_names,
            license=source.license,
            attribution=source.attribution,
            access_constraints=discovered.access_constraints,
            fees=discovered.fees,
            data_currency=discovered.data_currency,
            capabilities_hash=discovered.capabilities_hash,
        )
    )
    try:
        staged, rejected, fetched = [], [], 0
        for layer in source.layers:
            fetched_at = datetime.now(UTC)
            frame = parse_pages(scraper.fetch_layer(layer))
            fetched += len(frame)
            result = HazardTransformer(
                FetchContext(source, layer, fetched_at)
            ).transform(frame)
            staged.extend(result.staged)
            rejected.extend(result.rejected)
        counts = loader.load(run_id, staged, rejected, fetched_count=fetched)
    except Exception as exc:
        loader.fail_run(run_id, f"{type(exc).__name__}: {exc}")
        raise
    loader.finish_run(run_id, counts)
    return counts


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Harvest Hesse mining/geohazard restrictions into iris_staging"
    )
    parser.add_argument(
        "--source", action="append", dest="sources", help="data source key (repeatable)"
    )
    parser.add_argument(
        "--fixture-dir",
        type=Path,
        default=DEFAULT_FIXTURE_DIR,
        help="directory served for fixture sources",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)
    loader = PostgisLoader(create_engine(settings.database.sqlalchemy_url()))
    failures = 0
    for key in args.sources or settings.sources:
        source = get_data_source(key)
        session = build_session(settings.http)
        if source.mode is HarvestMode.FIXTURE:
            mount_fixture(session, source.url, args.fixture_dir)
            logger.warning(
                "source %s runs in FIXTURE mode from %s: synthetic data, not real HLNUG data",
                key,
                args.fixture_dir,
            )
        try:
            counts = harvest_source(
                source, build_scraper(source, session, settings), loader
            )
            logger.info("source %s done: %s", key, counts)
        except Exception:
            failures += 1
            logger.exception("source %s failed", key)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
