\set ON_ERROR_STOP on
\pset footer off
\pset null '<NULL>'

\echo '== Real parcels: HVBG INSPIRE-WFS HE Flurstücke/Grundstücke ALKIS, layer cp:CadastralParcel, dl-de/zero-2.0, fetched 2026-10-28'
\echo '   https://inspire-hessen.de/ows/services/org.2.07247d95-adc7-4c7d-9c7a-ed17af855317_wfs'
\echo '   parcel_297:  06216400600297, Flurstück 297, GML 3.2 requested with srsName EPSG:25832'
\echo '   parcel_10_5: 061610001000100005, Flurstück 10/5, GeoJSON (RFC 7946 lon/lat; the native CRS is EPSG:4258)'
\set parcel_297 'POLYGON((559403.98 5657803.44, 559421.26 5657753.47, 559423.06 5657748.56, 559425.46 5657743.54, 559438.54 5657716.69, 559455.48 5657709.63, 559480.32 5657703.57, 559560.79 5657712.24, 559624.63 5657717.71, 559646.10 5657720.33, 559663.38 5657728.21, 559661.97 5657731.23, 559708.17 5657757.94, 559770.92 5657812.25, 559772.27 5657812.79, 559761.24 5657854.83, 559754.88 5657914.85, 559743.94 5657954.17, 559700.30 5657937.21, 559679.07 5657924.40, 559674.08 5657923.22, 559674.00 5657926.75, 559678.84 5657932.23, 559693.83 5657966.93, 559700.69 5657986.63, 559710.63 5658030.68, 559723.47 5658048.81, 559720.64 5658059.50, 559715.13 5658088.39, 559710.98 5658117.99, 559710.02 5658123.92, 559706.85 5658152.75, 559626.22 5658148.35, 559596.19 5658146.17, 559586.36 5658144.34, 559539.50 5658137.74, 559542.80 5658108.43, 559542.00 5658096.84, 559404.71 5658093.48, 559403.99 5658079.97, 559402.40 5658050.17, 559397.55 5657959.11, 559412.22 5657901.58, 559411.68 5657895.42, 559403.98 5657803.44))'
\set parcel_10_5 'POLYGON((9.4409760 51.6089225, 9.4410465 51.6089132, 9.4413689 51.6098649, 9.4404426 51.6099862, 9.4399995 51.6100442, 9.4383911 51.6096367, 9.4382109 51.6096066, 9.4375359 51.6096964, 9.4374294 51.6096669, 9.4372141 51.6094165, 9.4409760 51.6089225))'

\echo '== 1. Parcel overlap: restrictions intersecting a real ALKIS parcel delivered in EPSG:25832 (Flurstück 297)'
WITH parcel AS (SELECT ST_GeomFromText(:'parcel_297', 25832) AS geom)
SELECT r.source, r.restriction_category, r.classification_status, count(*) AS n,
       round(ST_Area(p.geom)) AS parcel_m2,
       string_agg(r.source_feature_id, ', ' ORDER BY r.source_feature_id::int) AS feature_ids
FROM iris_staging.mining_restrictions r, parcel p
WHERE ST_Intersects(r.geom, p.geom)
GROUP BY 1, 2, 3, p.geom;

\echo '== 2. Parcel overlap: real ALKIS parcel delivered in EPSG:4258 lon/lat, transformed to 25832 before the GIST-indexed test (Flurstück 10/5)'
WITH parcel AS (SELECT ST_Transform(ST_GeomFromText(:'parcel_10_5', 4258), 25832) AS geom)
SELECT r.source, r.source_feature_id, r.restriction_category,
       r.attributes->>'GEWINNUNGS' AS site_name, r.attributes->>'ABBAUART' AS extraction_type,
       r.classification_status, round(ST_Area(p.geom)) AS parcel_m2, r.source_url
FROM iris_staging.mining_restrictions r, parcel p
WHERE ST_Intersects(r.geom, p.geom)
ORDER BY r.source, r.source_feature_id;

\echo '== 3. Proximity: restrictions within 250 m of the Flurstück 297 boundary but outside it (EPSG:25832 units are metres)'
WITH parcel AS (SELECT ST_GeomFromText(:'parcel_297', 25832) AS geom)
SELECT r.restriction_category, count(*) AS n,
       round(min(ST_Distance(r.geom, p.geom))::numeric, 1) AS nearest_m,
       round(max(ST_Distance(r.geom, p.geom))::numeric, 1) AS farthest_m
FROM iris_staging.mining_restrictions r, parcel p
WHERE ST_DWithin(r.geom, p.geom, 250)
  AND NOT ST_Intersects(r.geom, p.geom)
GROUP BY 1
ORDER BY 1;

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
