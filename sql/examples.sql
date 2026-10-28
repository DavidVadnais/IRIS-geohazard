\set ON_ERROR_STOP on
\pset footer off
\pset null '<NULL>'

\echo '== 1. Parcel overlap: restrictions intersecting a parcel given in EPSG:25832'
SELECT r.source, r.source_feature_id, r.restriction_category, r.status,
       r.hazard_classification, r.classification_status,
       round(ST_Area(ST_Intersection(r.geom, p.geom))::numeric, 1) AS overlap_m2,
       r.source_url
FROM iris_staging.mining_restrictions r
JOIN (SELECT ST_GeomFromText('POLYGON((477300 5552200, 477500 5552200, 477500 5552400, 477300 5552400, 477300 5552200))', 25832) AS geom) p
  ON ST_Intersects(r.geom, p.geom)
ORDER BY overlap_m2 DESC;

\echo '== 2. Parcel overlap: parcel delivered in WGS84 (EPSG:4326), transformed to 25832 before the GIST-indexed test'
WITH parcel AS (
    SELECT ST_Transform(ST_GeomFromText(
        'POLYGON((9.4864 51.3144, 9.4893 51.3144, 9.4893 51.3162, 9.4864 51.3162, 9.4864 51.3144))', 4326), 25832) AS geom
)
SELECT r.source, r.source_feature_id, r.restriction_category, r.classification_status
FROM iris_staging.mining_restrictions r, parcel p
WHERE ST_Intersects(r.geom, p.geom)
ORDER BY r.source, r.source_feature_id;

\echo '== 3. Proximity: restrictions within 250 m of a site (EPSG:25832 units are metres)'
WITH site AS (SELECT ST_SetSRID(ST_MakePoint(534150, 5685100), 25832) AS geom)
SELECT r.source, r.source_feature_id, r.restriction_category,
       r.hazard_classification, r.classification_status,
       round(ST_Distance(r.geom, s.geom)::numeric, 1) AS distance_m
FROM iris_staging.mining_restrictions r, site s
WHERE ST_DWithin(r.geom, s.geom, 250)
ORDER BY distance_m;

\echo '== 4. Proximity: count live sinkholes within 1 km of a site, nearest first (KNN)'
WITH site AS (SELECT ST_SetSRID(ST_MakePoint(532300, 5714600), 25832) AS geom)
SELECT r.source_feature_id, r.attributes->>'AMT_NR' AS amt_nr,
       round(ST_Distance(r.geom, s.geom)::numeric, 1) AS distance_m
FROM iris_staging.mining_restrictions r, site s
WHERE r.restriction_category = 'sinkhole_subsidence'
  AND ST_DWithin(r.geom, s.geom, 1000)
ORDER BY r.geom <-> s.geom
LIMIT 10;

\echo '== 5. The GIST index is used by ST_DWithin'
EXPLAIN (COSTS OFF)
SELECT id FROM iris_staging.mining_restrictions
WHERE ST_DWithin(geom, ST_SetSRID(ST_MakePoint(532300, 5714600), 25832), 1000);

\echo '== V1. Inventory by source, category and classification status'
SELECT source, restriction_category, classification_status, count(*) AS n
FROM iris_staging.mining_restrictions
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;

\echo '== V2. Geometry integrity (expect all zeros)'
SELECT count(*) FILTER (WHERE NOT ST_IsValid(geom)) AS invalid,
       count(*) FILTER (WHERE ST_IsEmpty(geom)) AS empty,
       count(*) FILTER (WHERE ST_SRID(geom) <> 25832) AS wrong_srid,
       count(*) FILTER (WHERE NOT ST_Within(geom, ST_MakeEnvelope(400000, 5460000, 600000, 5740000, 25832))) AS outside_hesse_bbox,
       count(*) FILTER (WHERE geom_was_repaired) AS repaired_kept
FROM iris_staging.mining_restrictions;

\echo '== V3. Duplicate stable keys (expect no rows)'
SELECT source, source_feature_id, count(*)
FROM iris_staging.mining_restrictions
GROUP BY 1, 2
HAVING count(*) > 1;

\echo '== V4. Provenance completeness: every row traceable to its source (expect all zeros)'
SELECT count(*) FILTER (WHERE r.source_url IS NULL OR r.source_url = '') AS no_source_url,
       count(*) FILTER (WHERE r.layer_name IS NULL OR r.layer_name = '') AS no_layer,
       count(*) FILTER (WHERE r.license IS NULL OR r.license = '') AS no_license,
       count(*) FILTER (WHERE r.fetched_at IS NULL) AS no_fetched_at,
       count(*) FILTER (WHERE r.last_run_id IS NULL OR h.run_id IS NULL) AS no_run
FROM iris_staging.mining_restrictions r
LEFT JOIN iris_staging.harvest_runs h ON h.run_id = r.last_run_id;

\echo '== V5. Missing classification is never stored as a value (expect zero)'
SELECT count(*) AS missing_with_value
FROM iris_staging.mining_restrictions
WHERE classification_status = 'missing' AND hazard_classification IS NOT NULL;

\echo '== V6. Classification values as published (missing shown separately, never folded into a low class)'
SELECT restriction_category, coalesce(hazard_classification, '<missing>') AS classification, classification_status, count(*)
FROM iris_staging.mining_restrictions
WHERE source = 'hlnug_demo'
GROUP BY 1, 2, 3
ORDER BY 1, 2;

\echo '== V7. Rejected records of the latest run per source, by reason'
WITH latest AS (
    SELECT DISTINCT ON (source) run_id, source FROM iris_staging.harvest_runs ORDER BY source, started_at DESC
)
SELECT j.source, j.reason_code, count(*) AS n, string_agg(coalesce(j.source_feature_id, '<no id>'), ', ' ORDER BY j.source_feature_id) AS feature_ids
FROM iris_staging.mining_restrictions_rejected j
JOIN latest l ON l.run_id = j.run_id
GROUP BY 1, 2
ORDER BY 1, 2;

\echo '== V8. Run log: legal/access metadata, data currency and counts per run'
SELECT source, mode, status, to_char(started_at, 'YYYY-MM-DD HH24:MI:SS') AS started,
       fetched_count AS fetched, inserted_count AS ins, updated_count AS upd, unchanged_count AS unch, rejected_count AS rej,
       license, access_constraints, fees, data_currency
FROM iris_staging.harvest_runs
ORDER BY started_at;

\echo '== V9. Idempotency: the latest run per source changed nothing when the source did not change'
SELECT DISTINCT ON (source) source, inserted_count, updated_count, unchanged_count
FROM iris_staging.harvest_runs
WHERE status = 'succeeded'
ORDER BY source, started_at DESC;
