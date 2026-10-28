#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PATH="$SCRIPT_DIR/$(basename "${BASH_SOURCE[0]}")"
cd "$SCRIPT_DIR"

PAUSE=true
FRESH=false
OPEN_MAP=true
SQL_OUT="$(mktemp)"
trap 'rm -f "$SQL_OUT"' EXIT

usage() {
    cat <<'EOF'
Usage: ./demo_of_codebase.sh [--fresh] [--yes] [--no-open]

Walks through the IRIS TASK 090 harvester step by step, naming the requirement each step checks.

  --fresh    delete the database volume first (docker compose down -v) so the first harvest inserts everything
  --yes      do not pause between steps
  --no-open  build the map but do not open it in a browser
EOF
}

for arg in "$@"; do
    case "$arg" in
        --fresh) FRESH=true ;;
        --yes) PAUSE=false ;;
        --no-open) OPEN_MAP=false ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            echo "unknown option: $arg" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ -t 1 ]]; then
    BOLD=$'\e[1m'
    DIM=$'\e[2m'
    CYAN=$'\e[36m'
    GREEN=$'\e[32m'
    YELLOW=$'\e[33m'
    RED=$'\e[31m'
    RESET=$'\e[0m'
else
    BOLD=""
    DIM=""
    CYAN=""
    GREEN=""
    YELLOW=""
    RED=""
    RESET=""
fi

STEP=0
TOTAL_STEPS="$(grep -c '^step "' "$SCRIPT_PATH")"

step() {
    STEP=$((STEP + 1))
    printf '\n%s%s━━ Step %d of %d: %s %s%s\n' "$BOLD" "$CYAN" "$STEP" "$TOTAL_STEPS" "$1" "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" "$RESET"
}

requirement() {
    printf '%sRequirement:%s %s\n' "$YELLOW" "$RESET" "$1"
}

say() {
    printf '%s\n' "$@"
}

show_cmd() {
    printf '%s$ %s%s\n' "$DIM" "$*" "$RESET"
}

pause() {
    if $PAUSE && [[ -t 0 ]]; then
        printf '\n%s[Enter to continue, Ctrl-C to stop]%s ' "$DIM" "$RESET"
        read -r _
    fi
}

checked() {
    printf '%s✔ %s%s\n' "$GREEN" "$1" "$RESET"
}

FAILED_CHECKS=0

check_sql() {
    local description="$1" query="$2" value
    value="$(printf '%s\n' "$query" | docker compose exec -T db sh -c 'psql -X -q -t -A -U "$POSTGRES_USER" -d "$POSTGRES_DB"')"
    if [[ "$value" == "t" ]]; then
        printf '%s✔ %s%s\n' "$GREEN" "$description" "$RESET"
    else
        printf '%s✘ %s (check returned %s)%s\n' "$RED" "$description" "${value:-nothing}" "$RESET"
        FAILED_CHECKS=$((FAILED_CHECKS + 1))
    fi
}

psql_db() {
    docker compose exec -T db sh -c 'psql -X -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
}

sql_section() {
    awk -v start="== $1" '
        index($0, start) == 1 { on = 1; print; next }
        on && /^== / { exit }
        on { print }
    ' "$SQL_OUT"
}

run_examples_sql() {
    psql_db <sql/examples.sql >"$SQL_OUT" 2>&1
}

step "Prerequisites"
say "Everything runs in Docker (Python 3.15 + PostGIS 16). Nothing is installed on the host."
command -v docker >/dev/null || {
    echo "docker is required" >&2
    exit 1
}
if [[ ! -f .env ]]; then
    say "No .env found: creating one from .env.example with a random database password."
    password="$(openssl rand -hex 24)"
    sed "s/^POSTGRES_PASSWORD=$/POSTGRES_PASSWORD=${password}/; s/^IRIS_DATABASE__PASSWORD=$/IRIS_DATABASE__PASSWORD=${password}/" \
        .env.example >.env
fi
checked "docker available, .env present (secrets stay out of git)"
pause

step "The data source named in the task"
requirement "Review OGC WFS capabilities and extract the relevant licensed mine-right, shaft, cavity or contaminated-land features."
say "The task names https://gds.hessen.de/wfs/altlasten, layer he_bergbau_kavernen. Asking it for its WFS capabilities:"
show_cmd "curl 'https://gds.hessen.de/wfs/altlasten?service=WFS&request=GetCapabilities'"
status="$(curl -s -o /dev/null -w '%{http_code} %{content_type}' --max-time 20 \
    'https://gds.hessen.de/wfs/altlasten?service=WFS&request=GetCapabilities' || echo 'no response')"
say "  -> HTTP ${status}"
say "" \
    "It is not a WFS: gds.hessen.de is the HLBG Geodatenshop and answers with an error image." \
    "Searching further (docs/source_discovery.md) found:" \
    "  * No public service for mine rights, shafts, cavities or contaminated land exists in Hesse." \
    "  * RP Darmstadt gives old-mining information only on application, for a fee, per parcel, to the owner:" \
    "    https://rp-darmstadt.hessen.de/umwelt-und-energie/bergbau/ich-und-mein-haus/altbergbau" \
    "So the build harvests three sources through the same pipeline:" \
    "  1. hlnug_demo: a synthetic WFS 2.0 (fixtures/hlnug_demo) covering the four required categories" \
    "  2. HLNUG Erdfälle und Senkungsmulden (live, 4691 sinkholes, ArcGIS REST)" \
    "  3. HLNUG Abbaustellen (live, 276 extraction sites, ArcGIS REST)"
pause

step "Build the image and start PostGIS"
if $FRESH; then
    if $PAUSE && [[ -t 0 ]]; then
        printf '%s--fresh deletes the database volume. Type "yes" to continue:%s ' "$YELLOW" "$RESET"
        read -r answer
        [[ "$answer" == "yes" ]] || {
            echo "aborted"
            exit 1
        }
    fi
    show_cmd "docker compose down -v"
    docker compose down -v
fi
show_cmd "docker compose build && docker compose up -d --wait db"
docker compose build --quiet
docker compose up -d --wait db
checked "image built, database healthy"
pause

step "Schema: everything lives in iris_staging"
requirement "Write only to iris_staging.mining_restrictions with stable ID/state keys."
say "Alembic creates the schema. The harvested features go to mining_restrictions; rejected records and the run log" \
    "get their own tables, all inside iris_staging."
show_cmd "alembic upgrade head"
docker compose run --rm harvester "alembic upgrade head" 2>&1 | grep -v "^ Container" || true
echo "SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema = 'iris_staging' ORDER BY 2;" | psql_db
echo "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'iris_staging.mining_restrictions'::regclass AND contype IN ('u', 'c') ORDER BY 1;" | psql_db
check_sql "UNIQUE (source, source_feature_id) is the stable key; CHECKs enforce valid geometry and consistent classification" \
    "SELECT count(*) FILTER (WHERE conname IN ('mining_restrictions_source_feature_uq', 'mining_restrictions_geom_valid', 'mining_restrictions_classification_consistent')) = 3 FROM pg_constraint WHERE conrelid = 'iris_staging.mining_restrictions'::regclass;"
pause

step "Harvest: capabilities, legal metadata and rejected geometries"
requirement "Log legal/access restrictions, data currency, source URL and inconsistent or null geometries."
say "The runner reads GetCapabilities and DescribeFeatureType, pages through GetFeature, and logs each run's license," \
    "access constraints, fees and data currency. Every rejected feature is logged at WARNING with its reason."
show_cmd "docker compose run --rm harvester"
docker compose run --rm harvester 2>&1 |
    grep -E "run started|rejected source|repaired invalid|FIXTURE mode|done:|ERROR" |
    sed -E 's/^[0-9-]+ [0-9:,]+ //'
check_sql "every source has a succeeded run with license, access constraints and data currency recorded" \
    "SELECT bool_and(status = 'succeeded' AND license IS NOT NULL AND access_constraints IS NOT NULL AND data_currency IS NOT NULL) FROM (SELECT DISTINCT ON (source) * FROM iris_staging.harvest_runs ORDER BY source, started_at DESC) latest;"
pause

step "Mapping: identifiers, type, status and published classification"
requirement "Map feature identifiers, mining/hazard type, status and published hazard classification; distinguish missing metadata from a safe classification."
say "Raw source attributes (left) next to the mapped columns (right) for the synthetic WFS layer:"
psql_db <<'EOF'
SELECT source_feature_id AS id,
       attributes->>'OBJEKTART' AS raw_type, restriction_category AS category,
       attributes->>'STATUS' AS raw_status, status,
       coalesce(attributes->>'GEFAEHRDUNGSKLASSE', '<absent/null>') AS raw_class,
       coalesce(hazard_classification, '<NULL>') AS hazard_classification, classification_status
FROM iris_staging.mining_restrictions
WHERE source = 'hlnug_demo'
ORDER BY 1;
EOF
run_examples_sql
sql_section "V5."
say "Null, blank, 'unbekannt', 'k.A.' and similar become NULL with status 'missing'. They are never stored as a low" \
    "or safe class, and a CHECK constraint makes that impossible. Unknown values are kept verbatim as 'unrecognised'."
check_sql "no row marked 'missing' carries a classification value (V5 = 0)" \
    "SELECT count(*) = 0 FROM iris_staging.mining_restrictions WHERE classification_status = 'missing' AND hazard_classification IS NOT NULL;"
pause

step "Geometry: type, topology and EPSG:25832"
requirement "Validate geometry type and topology, transform to EPSG:25832."
sql_section "V2."
psql_db <<'EOF'
SELECT source_feature_id AS id, geom_type, source_crs, geom_was_repaired, ST_SRID(geom) AS srid, ST_IsValid(geom) AS valid
FROM iris_staging.mining_restrictions
WHERE source = 'hlnug_demo' AND (geom_was_repaired OR source_feature_id IN ('S-0001', 'A-0001'))
ORDER BY 1;
EOF
say "H-0002 was a self-intersecting 'bow-tie' polygon: repaired with make_valid and flagged. Unrepairable shapes are" \
    "rejected instead (next step). Inputs in EPSG:31467, 4326 and 4258 are reprojected; a missing CRS is never assumed."
check_sql "all stored geometries are valid, non-empty, SRID 25832 and inside the Hesse bbox (V2)" \
    "SELECT bool_and(ST_IsValid(geom) AND NOT ST_IsEmpty(geom) AND ST_SRID(geom) = 25832 AND ST_Within(geom, ST_MakeEnvelope(400000, 5460000, 600000, 5740000, 25832))) FROM iris_staging.mining_restrictions;"
pause

step "Rejected records are visible"
requirement "Log ... inconsistent or null geometries (and keep bad records out of the staging table)."
sql_section "V7."
say "Each reject keeps its raw attributes and raw geometry WKT in iris_staging.mining_restrictions_rejected."
check_sql "the latest synthetic run rejected exactly the 6 planted bad features, each with a reason" \
    "SELECT count(*) = 6 AND bool_and(reason_code IS NOT NULL) FROM iris_staging.mining_restrictions_rejected WHERE run_id = (SELECT run_id FROM iris_staging.harvest_runs WHERE source = 'hlnug_demo' ORDER BY started_at DESC LIMIT 1);"
pause

step "Repeatable upserts"
requirement "Stable ID/state keys and repeatable upserts."
say "Running the harvester again: rows are only updated when their content hash changes."
show_cmd "docker compose run --rm harvester"
docker compose run --rm harvester 2>&1 | grep -E "done:" | sed -E 's/^[0-9-]+ [0-9:,]+ //'
run_examples_sql
sql_section "V9."
sql_section "V3."
check_sql "the latest run of every source inserted 0 and updated 0 (V9)" \
    "SELECT bool_and(inserted_count = 0 AND updated_count = 0) FROM (SELECT DISTINCT ON (source) * FROM iris_staging.harvest_runs WHERE status = 'succeeded' ORDER BY source, started_at DESC) latest;"
check_sql "no duplicate (source, source_feature_id) keys (V3)" \
    "SELECT count(*) = 0 FROM (SELECT 1 FROM iris_staging.mining_restrictions GROUP BY source, source_feature_id HAVING count(*) > 1) d;"
pause

step "Provenance: every row traceable to its source"
requirement "Log legal/access restrictions, data currency, source URL."
sql_section "V4."
psql_db <<'EOF'
SELECT DISTINCT ON (source) source, mode, status, license, access_constraints, data_currency
FROM iris_staging.harvest_runs
ORDER BY source, started_at DESC;
SELECT source, source_feature_id, layer_name, license, to_char(fetched_at, 'YYYY-MM-DD HH24:MI') AS fetched_at, source_url
FROM iris_staging.mining_restrictions
WHERE (source, source_feature_id) IN (('hlnug_demo', 'S-0001'), ('hlnug:geologie/geogefahren/20', '3823'), ('hlnug:geologie/rohstoffe/1', '1'))
ORDER BY source;
EOF
check_sql "every row has a source URL, layer, license, fetch time and a valid run ID (V4)" \
    "SELECT count(*) = 0 FROM iris_staging.mining_restrictions r LEFT JOIN iris_staging.harvest_runs h ON h.run_id = r.last_run_id WHERE coalesce(r.source_url, '') = '' OR coalesce(r.layer_name, '') = '' OR coalesce(r.license, '') = '' OR r.fetched_at IS NULL OR h.run_id IS NULL;"
pause

step "Parcel overlap and proximity on real ALKIS parcels"
requirement "Support parcel-overlap/proximity queries."
say "sql/examples.sql uses two real Hesse parcels from the HVBG INSPIRE ALKIS WFS (dl-de/zero-2.0), one delivered" \
    "in EPSG:25832 and one in EPSG:4258 that is transformed before the indexed test."
sql_section "1."
sql_section "2."
sql_section "3."
sql_section "5."
if grep -q "Index Scan on mining_restrictions_geom_gist" "$SQL_OUT"; then
    checked "the proximity query uses the GIST index (example 5)"
else
    printf '%s✘ the proximity query did not use the GIST index%s\n' "$RED" "$RESET"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
pause

step "Automated tests"
requirement "Edge cases are covered by tests, not only by this demo."
say "36 pytest tests with mocked WFS and ArcGIS responses: null, empty and invalid geometry, wrong or missing CRS," \
    "swapped axes, missing classification, paging, GML fallback, HTTP errors, duplicate runs and changed features."
show_cmd "python -m pytest -q"
if docker compose run --rm harvester "python -m pytest -q -p no:cacheprovider" >"$SQL_OUT" 2>&1; then
    checked "$(grep -E "passed" "$SQL_OUT" | tail -1)"
else
    tail -20 "$SQL_OUT"
    printf '%s✘ tests failed%s\n' "$RED" "$RESET"
    FAILED_CHECKS=$((FAILED_CHECKS + 1))
fi
pause

step "The map"
say "Builds an interactive map of Germany's 16 states with every iris_staging layer. Hesse is highlighted."
show_cmd "docker compose run --rm -v \"\$PWD\":/app harvester \"python -m visualize.make_map\""
docker compose run --rm -v "$PWD":/app harvester "python -m visualize.make_map" 2>&1 | grep -E "INFO|ERROR" | sed -E 's/^[0-9-]+ [0-9:,]+ //'
MAP="visualize/output/hesse_restrictions_map.html"
say "" \
    "What to try on the map:" \
    "  * Layer control (top right): each category is a toggle; synthetic layers are labelled as synthetic." \
    "  * Click a feature: ID, type, status, classification, license and a link to that exact feature at its source." \
    "    A missing classification is shown as 'MISSING (not published, not safe)'." \
    "  * Turn on 'Rejected records with geometry' to see the out-of-region and unrepairable features." \
    "  * Turn on 'ALKIS example parcels' and zoom to 9.85°E 51.07°N: Flurstück 297 and its sinkholes (example 1)." \
    "  * Click another state to zoom to it."
if $OPEN_MAP; then
    if command -v xdg-open >/dev/null; then
        xdg-open "$MAP" >/dev/null 2>&1 &
    elif command -v open >/dev/null; then
        open "$MAP"
    else
        say "Open $MAP in a browser."
    fi
fi
checked "map written to $MAP"

if [[ $FAILED_CHECKS -gt 0 ]]; then
    printf '\n%s%sDemo finished with %d failed check(s).%s\n' "$BOLD" "$RED" "$FAILED_CHECKS" "$RESET"
    exit 1
fi
printf '\n%s%sDemo complete: all checks passed.%s See README.md, docs/quality_report.md (requirement → evidence) and docs/source_discovery.md.\n' \
    "$BOLD" "$GREEN" "$RESET"
