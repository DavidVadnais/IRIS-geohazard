# IRIS-geohazard

IRIS TASK 090: a harvester for Hesse (Hessen) mine rights, shafts, cavities/caverns and contaminated land. It loads them into PostGIS staging (`iris_staging.mining_restrictions`) with per-row provenance, idempotent upserts and a visible table of rejected records.

> **Read this first: data availability.** There is **no public WFS** in Hesse for mine rights, shafts, cavities or
> contaminated land. The endpoint named in the task (`https://gds.hessen.de/wfs/altlasten`, layer `he_bergbau_kavernen`)
> does not exist. `gds.hessen.de` is the HLBG Geodatenshop, and the request returns HTTP 404. The four required categories
> are therefore delivered as a **fixture-based WFS demonstrator** with synthetic data. Two **real HLNUG layers** are
> harvested live alongside it, as a real-data check of the same pipeline. Details are in
> [docs/source_discovery.md](docs/source_discovery.md).

## Contents

- [Provenance](#provenance)
- [Quick start](#quick-start)
- [Running](#running)
- [Configuration](#configuration)
- [Pipeline](#pipeline)
- [Schema](#schema)
- [Schema mapping](#schema-mapping)
- [Rules](#rules)
- [Example queries](#example-queries)
- [Map](#map)
- [Assumptions](#assumptions)
- [Limitations](#limitations)
- [Project layout](#project-layout)

## Provenance

| Source key | Mode | Endpoint | Layer | Category | License | Access |
|---|---|---|---|---|---|---|
| `hlnug_fixture_wfs` | fixture | `https://fixture.invalid/hlnug/wfs`, served from `fixtures/hlnug_demo/` | `hlnug_demo:he_bergbau_kavernen` | mine_right, shaft, cavity, contaminated_land | synthetic, none | none |
| `hlnug_geogefahren_erdfaelle` | live | `https://geodienste-umwelt.hessen.de/arcgis/rest/services/geologie/geogefahren/MapServer` | `20` Erdfälle und Senkungsmulden (Point, 4691 features) | sinkhole_subsidence | CC BY 4.0 (assumed, see below) | public, no restrictions |
| `hlnug_rohstoffe_abbaustellen` | live | `https://geodienste-umwelt.hessen.de/arcgis/rest/services/geologie/rohstoffe/MapServer` | `1` Abbaustellen (Point, 276 features) | extraction_site | CC BY 4.0 (assumed, see below) | public, no restrictions |

- **Attribution for live data:** © Hessisches Landesamt für Naturschutz, Umwelt und Geologie (HLNUG).
- **Live service details:**
  - ArcGIS Server 11.3, native EPSG:25832.
  - The WFS extension is not enabled, so features come through the ArcGIS REST `query` operation, paged, as GeoJSON.
- **License:**
  - HLNUG's published terms (`https://geodienste-umwelt.hessen.de/inspire/HLNUG_INSPIRE_Nutzungsbedingungen.txt`, Stand Mai 2025)
    state: no restrictions on public access, free private and commercial use, CC BY 4.0, not legally binding.
  - Those terms are written for HLNUG's INSPIRE services. Applying them to the `geologie/*` services is an **assumption**,
    and it is recorded as such in every row's `license`.
- **Not used:**
  - `haa/haa` (HTTP 403, restricted)
  - the Altflächendatei / FIS AG (not public)
  - Bergaufsicht (RP Darmstadt / Gießen / Kassel) mining data (no public service)
  - No access restriction was bypassed.
- **Per run:** source URL, layers, license, attribution, access constraints, fees, data currency and a capabilities hash are
  written to `iris_staging.harvest_runs` and logged at INFO.

## Quick start

Requirements: Docker with Compose and internet access (the live HLNUG layers, the state boundaries and the map tiles are
fetched online). Everything runs in containers (Python 3.15), so nothing needs to be installed on the host.

**Guided demo:** `./demo_of_codebase.sh` does all of the steps below in one go, pausing before each one. For every
step it prints the task requirement being checked, runs the real commands and queries, and marks each check ✔ or ✘. It
ends by building and opening the map. Options:
- `--fresh` wipes the database volume first, so the first harvest shows every insert. It asks you to type "yes".
- `--yes` runs without pauses. Combined with `--fresh`, it also skips the confirmation and wipes without asking.
- `--no-open` builds the map without opening a browser.

It creates `.env` with a random password if there is none, and exits 1 if any check fails.

**1. Configure.** Copy the env template and set one password, used twice:

```bash
cp .env.example .env
# edit .env: set POSTGRES_PASSWORD and IRIS_DATABASE__PASSWORD to the same value
```

**2. Build and start the database.**

```bash
docker compose build
docker compose up -d --wait db
```

**3. Harvest.** This runs the migrations, then all three sources (synthetic fixture, Erdfälle, Abbaustellen):

```bash
docker compose run --rm harvester
```

Expected: the fixture source gives 16 fetched, 10 inserted and 6 rejected; Erdfälle 4691 inserted; Abbaustellen 276
inserted. Running it again gives 0 inserted and 0 updated for every source.

**4. Build the map.** The `-v` mount writes the HTML back to the host:

```bash
docker compose run --rm -v "$PWD":/app harvester "python -m visualize.make_map"
xdg-open visualize/output/hesse_restrictions_map.html
```

On a Mac, use `open` instead of `xdg-open`, or open the file in any browser. On the map, toggle the categories in the
layer control at the top right, click a feature for its details and source link, and click a state to zoom to it. See
[Map](#map) for the options.

**5. Optional checks.**

```bash
docker compose run --rm -v "$PWD":/app harvester "python -m pytest -v -p no:cacheprovider"
docker compose exec -T db sh -c 'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/examples.sql
```

**Stop:** `docker compose down`. Add `-v` to also delete the database volume.

## Running

| Task | Command |
|---|---|
| Harvest all configured sources | `docker compose run --rm harvester` |
| One source only | `docker compose run --rm harvester "python -m geohazard.runner --source hlnug_fixture_wfs"` |
| Offline (fixture only) | same as above; the fixture source never touches the network |
| Tests (needs `db` running) | `docker compose run --rm -v "$PWD":/app harvester "python -m pytest -v -p no:cacheprovider"` |
| Example + validation SQL | `docker compose exec -T db sh -c 'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/examples.sql` |
| Migrations only | `docker compose run --rm harvester "alembic upgrade head"` |
| Stop / wipe | `docker compose down` / `docker compose down -v` |
| Python lint and format check | `pre-commit run --all-files` (ruff check and ruff format, pinned in `.pre-commit-config.yaml`) |
| Shell script checks (CI only, optional locally) | `shellcheck demo_of_codebase.sh && shfmt -d demo_of_codebase.sh` (CI uses shellcheck 0.11.0 and shfmt 3.14.1; style from `.editorconfig`) |
| Enable the pre-commit hook (once per clone) | `pre-commit install`; commits are then blocked until ruff check and ruff format pass |

- **Exit codes:** the runner exits `0` when every source succeeds and `1` when any source fails. A failed source is recorded as
  `status = 'failed'` with its error in `harvest_runs`, and nothing from it is loaded.
- **Test database:** database tests run in a separate `<POSTGRES_DB>_test` database, created automatically, so they never
  touch harvested data. If PostGIS is unreachable, the database tests are skipped with a message.

<details>
<summary>Local run without Docker (optional)</summary>

```bash
uv python install 3.15
uv venv --python 3.15 && . .venv/bin/activate
pip install -r requirements-dev.txt -c requirements.lock
export IRIS_DATABASE__HOST=localhost IRIS_DATABASE__PASSWORD=... IRIS_CONFIG=config.example.yaml
alembic upgrade head && python -m geohazard.runner
```

</details>

## Configuration

- **Settings sources:** `geohazard/config/settings.py` reads environment variables (`IRIS_` prefix, `__` for nesting) on top of
  the YAML file named by `IRIS_CONFIG` (default `/app/config.example.yaml`).
- **Secrets:** they come only from the environment (`.env`, which is gitignored). `.env.example` and `config.example.yaml` contain
  no secrets.

| Setting | Env var | Default |
|---|---|---|
| Sources to run | `IRIS_SOURCES` (JSON list) | `config.example.yaml` lists all three |
| DB host/port/name/user/password | `IRIS_DATABASE__HOST` … `IRIS_DATABASE__PASSWORD` | `localhost` / `5432` / `iris` / `iris` / empty |
| HTTP timeout, retries, backoff, page size | `IRIS_HTTP__TIMEOUT_SECONDS` … `IRIS_HTTP__PAGE_SIZE` | 60 s, 3, 1.5, 1000 |
| Log level | `IRIS_LOG_LEVEL` | `INFO` |

Source definitions live in `geohazard/config/data_sources.py`: URL, layers, id/category/status/classification fields,
category and status maps, classification vocabulary, license and access text.

## Pipeline

The structure follows the iris-sample repo's layout (scraper, parser, transformer, loader, runner).

```
scraper  ──►  parser  ──►  transformer  ──►  loader
WFS 2.0 /     GeoJSON /    map, validate,    upsert + rejects
ArcGIS REST   GML → GDF    reproject, hash   + run log
```

1. **Scraper** (`geohazard/scrapers/`):
   - `WfsScraper`:
     - parses GetCapabilities with OWSLib and fails if the layer is not offered
     - reads DescribeFeatureType and fails if the id field is missing
     - pages through GetFeature with `count`/`startIndex`, using JSON when offered and GML 3.2 otherwise
   - `ArcgisRestScraper` pages through `query` with `outSR=25832` and `exceededTransferLimit`.
   - Both retry on 429 and 5xx errors. `FixtureAdapter` serves `fixtures/hlnug_demo/` to `requests`, so fixture mode runs
     exactly the live code path.
2. **Parser** (`geohazard/parsers/feature_parser.py`):
   - Produces a GeoDataFrame per layer, with the CRS taken from the response (the GeoJSON `crs` member, or GML `srsName` via
     pyogrio).
   - If pages of one layer declare different CRSs, the layer fails.
3. **Transformer** (`geohazard/transformers/hazard_transformer.py`): applies the [rules](#rules).
4. **Loader** (`geohazard/loaders/postgis_loader.py`):
   - The run row is written in its own transaction.
   - All staged and rejected rows of a run go in one transaction, so a failure leaves nothing partial.

## Schema

Migration: `migrations/versions/0001_create_iris_staging.py`. Every object, including `alembic_version`, lives in `iris_staging`.

### `iris_staging.mining_restrictions`

| Column | Notes |
|---|---|
| `source`, `source_feature_id` | **Stable key**, `UNIQUE (source, source_feature_id)` |
| `restriction_category` | CHECK: `mine_right`, `shaft`, `cavity`, `contaminated_land`, `sinkhole_subsidence`, `extraction_site` |
| `hazard_type` | raw source type value (e.g. `Kaverne`, `ABBAUART`) |
| `status`, `status_raw` | normalised status (NULL if unmapped) and the verbatim source value |
| `hazard_classification` | the published value verbatim; **NULL when not published** |
| `classification_status` | `published` / `missing` / `unrecognised`; CHECK ties `missing` to a NULL classification |
| `attributes` | all raw source properties (jsonb) |
| `geom` | `geometry(Geometry, 25832)`, **GIST index**, CHECK `ST_IsValid AND NOT ST_IsEmpty` |
| `geom_type`, `geom_was_repaired`, `source_crs` | geometry provenance |
| `content_hash` | SHA-256 of the mapped fields, attributes, license, per-feature URL and normalised WKB |
| `source_url` | **per-feature URL** (WFS `GetFeature&resourceId=<id>`, or ArcGIS `query?where=<id_field>=<id>`) |
| `layer_name`, `license`, `fetched_at` | per-row provenance |
| `first_seen_at`, `last_changed_at`, `last_run_id` | change tracking; `last_run_id` → `harvest_runs` |

### `iris_staging.mining_restrictions_rejected`

- **Columns:** `run_id`, `source`, `layer_name`, `source_feature_id` (nullable), `reason_code` (CHECK list),
  `reason_detail`, `raw_feature` (jsonb), `raw_geometry_wkt`, `source_url`, `fetched_at`, `rejected_at`.
- **One row per rejected feature per run.**

### `iris_staging.harvest_runs`

- **Columns:** `run_id`, `source_key`, `source`, `mode` (`live`/`fixture`), `status` (`running`/`succeeded`/`failed`),
  `started_at`, `finished_at`, `source_url`, `layer_names`, `license`, `attribution`, `access_constraints`, `fees`,
  `data_currency`, `capabilities_hash`, the counts (`fetched`, `inserted`, `updated`, `unchanged`, `rejected`), `error`.

## Schema mapping

| Target column | Fixture WFS `he_bergbau_kavernen` | Live `geogefahren/20` | Live `rohstoffe/1` |
|---|---|---|---|
| `source` | `hlnug_demo` | `hlnug:geologie/geogefahren/20` | `hlnug:geologie/rohstoffe/1` |
| `source_feature_id` | `OBJEKT_ID` | `OBJECTID` | `OBJECTID` |
| `restriction_category` | `OBJEKTART` via category map | fixed `sinkhole_subsidence` | fixed `extraction_site` |
| `hazard_type` | `OBJEKTART` | – | `ABBAUART` |
| `status_raw` → `status` | `STATUS` via status map | – | `GEWINNUNGS` (raw only) |
| `hazard_classification` | `GEFAEHRDUNGSKLASSE` | – (always `missing`) | – (always `missing`) |
| `attributes` | all properties | `OBJECTID`, `AMT_NR`, `TS_FLAE` | all 13 fields incl. `ABBAURECHT`, `ROHSTOFF`, `FIRMA` |

**Fixture category map** (`OBJEKTART`, matched case- and umlaut-insensitively):

| Source value | Category |
|---|---|
| Bergbauberechtigung, Bergwerkseigentum, Bewilligung | `mine_right` |
| Schacht, Stollen, Tagesöffnung | `shaft` |
| Hohlraum, Kaverne, Grubenbau | `cavity` |
| Altfläche, Altablagerung, Altstandort, Altlast | `contaminated_land` |

Anything else is rejected as `unmapped_category`.

**Fixture status map:**

| Source value | Status |
|---|---|
| aktiv, in Betrieb | `active` |
| stillgelegt | `abandoned` |
| erloschen | `expired` |
| verfüllt | `backfilled` |
| saniert | `remediated` |
| in Bearbeitung | `under_investigation` |
| altlastverdächtig | `suspected` |

**Fixture classification vocabulary:** `hoch`, `mittel`, `gering`, `Keine Gefährdung bekannt`.

## Rules

**Classification:** a missing value is never treated as safe.

| Source value | `hazard_classification` | `classification_status` |
|---|---|---|
| a vocabulary value, e.g. `gering` | `gering` (verbatim) | `published` |
| any other non-empty value, e.g. `Klasse 7` | verbatim | `unrecognised` |
| null, absent field, `""`, `unbekannt`, `k.A.`, `n.b.`, `-` | **NULL** | `missing` |
| source has no classification field (both live layers) | **NULL** | `missing` |

- No code path writes a default class.
- The database CHECK makes `missing` with a value impossible.
- "Keine Gefährdung bekannt" is stored only when the source publishes it.

**Checks, in order.** Each failure lands in `mining_restrictions_rejected` with its `reason_code` and is logged at WARNING
with the feature ID.

| # | Check | Reject reason |
|---|---|---|
| 1 | id field present | `missing_feature_id` |
| 2 | id not already seen in this fetch (first occurrence kept) | `duplicate_feature_id` |
| 3 | response declares a CRS in {25832, 31467, 4326, 4258} | `unknown_crs` (a CRS is never assumed) |
| 4 | category maps | `unmapped_category` |
| 5 | geometry not null | `null_geometry` |
| 6 | geometry not empty | `empty_geometry` |
| 7 | geometry type allowed for the layer | `unsupported_geometry_type` |
| 8 | valid, or repairable with `make_valid(method="structure")` (flagged `geom_was_repaired`) | `invalid_geometry_unrepairable` |
| 9 | after reprojection to EPSG:25832, inside the Hesse bbox (400000 5460000, 600000 5740000) | `out_of_region` (catches a wrong CRS or swapped axes) |
| 10 | still valid and non-empty at 1 mm precision | `invalid_geometry_unrepairable` |

**Idempotency:**

```sql
INSERT ... ON CONFLICT (source, source_feature_id) DO UPDATE SET ...
WHERE mining_restrictions.content_hash IS DISTINCT FROM EXCLUDED.content_hash
RETURNING (xmax = 0) AS inserted
```

- The hash excludes `fetched_at` and `run_id`, so unchanged features are left untouched, including their `last_run_id` and
  `last_changed_at`.
- A re-run produces 0 inserted and 0 updated. A changed feature is updated exactly once.

## Example queries

[`sql/examples.sql`](sql/examples.sql) contains the parcel-overlap and proximity examples and nine validation checks. The
captured output is in [`docs/validation_output.txt`](docs/validation_output.txt).

The parcel examples use **real Hesse cadastral parcels**. They come from the HVBG
[INSPIRE-WFS HE Flurstücke/Grundstücke ALKIS](https://inspire-hessen.de/ows/services/org.2.07247d95-adc7-4c7d-9c7a-ed17af855317_wfs?SERVICE=WFS&REQUEST=GetCapabilities&VERSION=2.0.0)
(`cp:CadastralParcel`, dl-de/zero-2.0, no access restrictions), fetched on 2026-10-28. They are embedded in the script
as psql variables (`:'parcel_297'`, `:'parcel_10_5'`), so the examples write nothing to the database.
- **Flurstück 297** (`06216400600297`, about 9.85°E 51.07°N in northeast Hesse, 35 mapped sinkholes inside it) was requested as GML in EPSG:25832.
- **Flurstück 10/5** (`061610001000100005`, at the Würmersen sandstone quarry) was requested as GeoJSON. That comes back
  in lon/lat (RFC 7946) whatever `srsName` asks for, so it is read as EPSG:4258 and transformed.

```sql
-- parcel overlap (parcel in any CRS -> transform to 25832 so the GIST index is used)
SELECT r.source_feature_id, r.restriction_category, r.classification_status, r.source_url
FROM iris_staging.mining_restrictions r
WHERE ST_Intersects(r.geom, ST_Transform(ST_GeomFromText(:'parcel_10_5', 4258), 25832));

-- proximity in metres (EPSG:25832 is metric)
SELECT r.restriction_category, count(*) AS n
FROM iris_staging.mining_restrictions r,
     (SELECT ST_GeomFromText(:'parcel_297', 25832) AS geom) p
WHERE ST_DWithin(r.geom, p.geom, 250) AND NOT ST_Intersects(r.geom, p.geom)
GROUP BY 1;
```

Excerpt of the output:

```
== 2. Parcel overlap: real ALKIS parcel delivered in EPSG:4258 lon/lat, transformed to 25832 before the GIST-indexed test (Flurstück 10/5)
           source           | source_feature_id | restriction_category |         site_name          | extraction_type | classification_status | parcel_m2
----------------------------+-------------------+----------------------+----------------------------+-----------------+-----------------------+-----------
 hlnug:geologie/rohstoffe/1 | 1                 | extraction_site      | Sandsteintagebau Würmersen | Trockenabbau    | missing               |     20352

== 3. Proximity: restrictions within 250 m of the Flurstück 297 boundary but outside it (EPSG:25832 units are metres)
 restriction_category | n  | nearest_m | farthest_m
----------------------+----+-----------+------------
 sinkhole_subsidence  | 65 |       0.6 |      248.0

== V2. Geometry integrity (expect all zeros)
 invalid | empty | wrong_srid | outside_hesse_bbox | repaired_kept
---------+-------+------------+--------------------+---------------
       0 |     0 |          0 |                  0 |             1
```

## Map

`visualize/make_map.py` renders everything in `iris_staging` onto an interactive map of Germany's 16 states. Hesse is
highlighted and zoomed by default.

```bash
docker compose up -d --wait db
docker compose run --rm -v "$PWD":/app harvester "python -m visualize.make_map"
xdg-open visualize/output/hesse_restrictions_map.html     # or open the file in any browser
```

- **States:** clicking a state zooms to it. `--state Bayern` (any VG2500 state name) changes the highlighted state.
- **Features:** every feature has a popup with category, ID, source, type, status, classification, CRS, license and a link to
  its per-feature source URL. A missing classification is shown as **MISSING (not published, not safe)**.
- **Layers:** each category is its own toggle, with counts, and synthetic layers are labelled as synthetic. A hidden-by-default
  layer shows the rejected records of the latest run that still have a geometry (e.g. the out-of-region and unrepairable ones).
- **Example parcels:** a second hidden-by-default layer, "ALKIS example parcels", draws the two real HVBG parcels used in
  `sql/examples.sql` (Flurstück 297 and 10/5) as magenta outlines, with Flurstück number, cadastral reference, official
  area and license in the popup. They come from `visualize/example_parcels.geojson` (committed, generated from the SQL
  variables so both use identical geometry); `--parcels <file>` draws a different GeoJSON instead. They are drawn only
  on the map and are not written to the database.
- **Sources:**
  - state boundaries: BKG VG2500 WFS (`vg2500:vg2500_lan`, land areas only), cached in `visualize/data/`; `--refresh-states`
    re-downloads them
  - basemap: BKG TopPlusOpen tiles
  - both are © GeoBasis-DE / BKG, dl-de/by-2-0, with no key and no access restrictions
- **Output:** a self-contained HTML file of about 5 MB. Leaflet and folium assets load from a CDN, so viewing it needs internet
  access. `visualize/output/` and `visualize/data/` are gitignored.
- **Dependency:** folium (pinned in `requirements-dev.txt` and `requirements.lock`).

## Assumptions

- **Fixture data:** the fixture data and schema (`OBJEKT_ID`, `OBJEKTART`, `STATUS`, `GEFAEHRDUNGSKLASSE`) are **synthetic**,
  shaped like a plausible HLNUG schema. A real source would need its own mapping in `data_sources.py`.
- **License:** CC BY 4.0 is assumed for the `geologie/*` services, based on HLNUG's INSPIRE terms. It is not confirmed per service.
- **Live feature IDs:** `OBJECTID` is used as the live feature ID. ArcGIS OBJECTIDs can change if HLNUG republishes a layer;
  `AMT_NR` / `KRS_NR` were not confirmed unique.
- **Region check:** the Hesse check is a bounding box, not the state boundary. Features just across the border inside the box
  pass it.
- **GeoJSON CRS:** a GeoJSON response without a `crs` member is treated as **unknown**, not as RFC 7946 WGS84. This is
  deliberately strict.

## Limitations

- **Categories:** no real data exists for the four required categories, because no public source exists (see the notice above).
  The live layers are geohazard and extraction data, not mine rights, shafts or contaminated land.
- **Old mining (Altbergbau) is only available per parcel, on request:** the Hesse mining authority, RP Darmstadt
  (Bergaufsicht), gives information on old mining and the risk of mining damage
  [only on application, for a fee, for one parcel at a time](https://rp-darmstadt.hessen.de/umwelt-und-energie/bergbau/ich-und-mein-haus/altbergbau).
  The application must include a site plan and a land-register extract (Grundbuchauszug) proving ownership, or the
  owner's authorisation. The mining authority's own data on shafts, tunnels and cavities therefore can't be harvested;
  it is released case by case, not published as a dataset or service. The Hesse open-data catalogue
  (`geoportal.hessen.de/spatial-objects/`, 243 services) also lists no mining, Altbergbau or contaminated-land service
  (see [docs/source_discovery.md](docs/source_discovery.md) §9).
- **Classification:** neither live layer publishes a hazard classification, so every live row is `missing`.
- **Disappeared features:** features removed from the source are not deleted or flagged; there is no `last_seen` tracking yet.
- **Rejected rows:** these are a per-run log, so the same bad feature appears again in every run.
- **Data currency:** the WFS fixture and the live services state no data currency (`not stated by service`). ArcGIS
  `editingInfo.lastEditDate` is used when a service provides it.
- **Region and geometry checks:** the Hesse check is a bounding box, not the state boundary. Geometry types are not normalised
  to Multi*; the actual type is stored in `geom_type`.

## Project layout

```
geohazard/
  config/settings.py, config/data_sources.py
  scrapers/base.py, wfs_scraper.py, arcgis_rest_scraper.py, fixture_adapter.py
  parsers/feature_parser.py
  transformers/hazard_transformer.py
  loaders/tables.py, postgis_loader.py
  runner.py, logging_config.py
migrations/                 alembic env + 0001_create_iris_staging
fixtures/hlnug_demo/        synthetic WFS (capabilities, DescribeFeatureType, GetFeature)
tests/                      unit, loader and end-to-end tests; fixtures/wfs, fixtures/arcgis
demo_of_codebase.sh         guided walkthrough: each requirement, its evidence, then the map
sql/examples.sql            example + validation queries
visualize/make_map.py       interactive map of German states + iris_staging layers (folium)
visualize/example_parcels.geojson
                            the two ALKIS parcels from sql/examples.sql, drawn on the map
docs/                       source_discovery.md, quality_report.md, validation_output.txt, test_output.txt
requirements.txt, requirements-dev.txt, requirements.lock (all pins), pyproject.toml
Dockerfile, docker-compose.yml, .env.example, config.example.yaml
```
