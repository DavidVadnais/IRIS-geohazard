import logging
import uuid
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass
from itertools import islice
from typing import Any

from geoalchemy2.shape import from_shape
from sqlalchemy import Engine, func, insert, literal_column, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from geohazard.loaders.tables import (
    harvest_runs,
    mining_restrictions,
    mining_restrictions_rejected,
)
from geohazard.transformers.hazard_transformer import RejectedRecord, StagedRecord

logger = logging.getLogger(__name__)

BATCH_SIZE = 500
TARGET_SRID = 25832

UPSERT_UPDATE_COLUMNS = (
    "restriction_category",
    "hazard_type",
    "status",
    "status_raw",
    "hazard_classification",
    "classification_status",
    "attributes",
    "geom",
    "geom_type",
    "geom_was_repaired",
    "source_crs",
    "content_hash",
    "source_url",
    "layer_name",
    "license",
    "fetched_at",
)


@dataclass(frozen=True)
class RunMetadata:
    source_key: str
    source: str
    mode: str
    source_url: str
    layer_names: list[str]
    license: str
    attribution: str | None = None
    access_constraints: str | None = None
    fees: str | None = None
    data_currency: str | None = None
    capabilities_hash: str | None = None


@dataclass(frozen=True)
class LoadCounts:
    fetched: int
    inserted: int
    updated: int
    unchanged: int
    rejected: int


def _batches(items: Sequence[Any], size: int) -> Iterator[list[Any]]:
    iterator = iter(items)
    while batch := list(islice(iterator, size)):
        yield batch


def _staged_row(record: StagedRecord, run_id: uuid.UUID) -> dict[str, Any]:
    row = asdict(record)
    row["geom"] = from_shape(record.geom, srid=TARGET_SRID)
    row["last_run_id"] = run_id
    return row


def _rejected_row(record: RejectedRecord, run_id: uuid.UUID) -> dict[str, Any]:
    return {**asdict(record), "run_id": run_id}


class PostgisLoader:
    def __init__(self, engine: Engine):
        self.engine = engine

    def start_run(self, metadata: RunMetadata) -> uuid.UUID:
        run_id = uuid.uuid4()
        with self.engine.begin() as connection:
            connection.execute(
                insert(harvest_runs).values(
                    run_id=run_id, status="running", **asdict(metadata)
                )
            )
        logger.info(
            "run started run_id=%s source=%s mode=%s url=%s layers=%s license=%r access_constraints=%r fees=%r data_currency=%r",
            run_id,
            metadata.source,
            metadata.mode,
            metadata.source_url,
            metadata.layer_names,
            metadata.license,
            metadata.access_constraints,
            metadata.fees,
            metadata.data_currency,
        )
        return run_id

    def load(
        self,
        run_id: uuid.UUID,
        staged: Sequence[StagedRecord],
        rejected: Iterable[RejectedRecord],
        fetched_count: int,
    ) -> LoadCounts:
        rejected = list(rejected)
        inserted = updated = 0
        with self.engine.begin() as connection:
            for batch in _batches(staged, BATCH_SIZE):
                statement = pg_insert(mining_restrictions).values(
                    [_staged_row(r, run_id) for r in batch]
                )
                changed_set = {
                    column: statement.excluded[column]
                    for column in UPSERT_UPDATE_COLUMNS
                }
                changed_set["last_changed_at"] = func.now()
                changed_set["last_run_id"] = statement.excluded.last_run_id
                statement = statement.on_conflict_do_update(
                    constraint="mining_restrictions_source_feature_uq",
                    set_=changed_set,
                    where=mining_restrictions.c.content_hash.is_distinct_from(
                        statement.excluded.content_hash
                    ),
                ).returning(literal_column("(xmax = 0)").label("inserted"))
                for (was_inserted,) in connection.execute(statement):
                    if was_inserted:
                        inserted += 1
                    else:
                        updated += 1
            for batch in _batches(rejected, BATCH_SIZE):
                connection.execute(
                    insert(mining_restrictions_rejected),
                    [_rejected_row(r, run_id) for r in batch],
                )
        return LoadCounts(
            fetched=fetched_count,
            inserted=inserted,
            updated=updated,
            unchanged=len(staged) - inserted - updated,
            rejected=len(rejected),
        )

    def finish_run(self, run_id: uuid.UUID, counts: LoadCounts) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(harvest_runs)
                .where(harvest_runs.c.run_id == run_id)
                .values(
                    status="succeeded",
                    finished_at=func.now(),
                    fetched_count=counts.fetched,
                    inserted_count=counts.inserted,
                    updated_count=counts.updated,
                    unchanged_count=counts.unchanged,
                    rejected_count=counts.rejected,
                )
            )
        logger.info("run succeeded run_id=%s %s", run_id, asdict(counts))

    def fail_run(self, run_id: uuid.UUID, error: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(harvest_runs)
                .where(harvest_runs.c.run_id == run_id)
                .values(status="failed", finished_at=func.now(), error=error[:4000])
            )
        logger.error("run failed run_id=%s error=%s", run_id, error)
