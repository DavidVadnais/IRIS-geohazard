# Quality and error report: IRIS TASK 090

Run date: 2026-10-28. Clean state (`docker compose down -v`), image built, default entrypoint run twice.
Evidence files: [validation_output.txt](validation_output.txt) (output of `sql/examples.sql`) and
[test_output.txt](test_output.txt) (`pytest -v`, **36 passed**).

## 1. Run summary

| Source | Mode | Fetched | Run 1: inserted / updated / unchanged / rejected | Run 2: inserted / updated / unchanged / rejected |
|---|---|---|---|---|
| `hlnug_demo` (synthetic WFS) | fixture | 16 | 10 / 0 / 0 / 6 | 0 / 0 / 10 / 6 |
| `hlnug:geologie/geogefahren/20` Erdfälle | live | 4691 | 4691 / 0 / 0 / 0 | 0 / 0 / 4691 / 0 |
| `hlnug:geologie/rohstoffe/1` Abbaustellen | live | 276 | 276 / 0 / 0 / 0 | 0 / 0 / 276 / 0 |

- Both runs exited with code 0.
- Run 2 re-fetched every source and changed nothing.

## 2. Rejected records by reason (latest run)

| Reason | Count | Feature | Cause |
|---|---|---|---|
| `null_geometry` | 1 | N-0001 | `geometry: null` |
| `empty_geometry` | 1 | E-0001 | `Polygon` with no coordinates |
| `invalid_geometry_unrepairable` | 1 | H-0003 | zero-area ring; `make_valid` gives no polygon |
| `unmapped_category` | 1 | U-0001 | `OBJEKTART = Steinbruch` is not in the category map |
| `duplicate_feature_id` | 1 | S-0001 (2nd) | same `OBJEKT_ID` twice in one response; the first is kept |
| `out_of_region` | 1 | O-0001 | point at 692000 5335000 (Bavaria) |
| `unknown_crs`, `missing_feature_id`, `unsupported_geometry_type` | 0 | – | covered by tests (§4) |
| **Total** | **6** of 16 fixture features | | the live layers had 0 rejects |

Every reject is stored in `iris_staging.mining_restrictions_rejected` with its run ID, reason detail, raw attributes, raw
geometry WKT and source URL. Each is also logged at WARNING. Query V7 lists them.

## 3. Accepted data (V1)

| Source | Category | published | unrecognised | missing |
|---|---|---|---|---|
| hlnug_demo | mine_right | 2 | 0 | 0 |
| hlnug_demo | shaft | 1 | 0 | 2 |
| hlnug_demo | cavity | 2 | 0 | 0 |
| hlnug_demo | contaminated_land | 1 | 1 | 1 |
| geogefahren/20 | sinkhole_subsidence | 0 | 0 | 4691 |
| rohstoffe/1 | extraction_site | 0 | 0 | 276 |

Geometry integrity (V2):

| Check | Result |
|---|---|
| invalid | 0 |
| empty | 0 |
| wrong SRID | 0 |
| outside the Hesse bbox | 0 |
| repaired and kept (H-0002, a bow-tie made into a MultiPolygon) | 1 |

## 4. Edge cases covered

| Edge case | Handling | Evidence |
|---|---|---|
| Null geometry | rejected `null_geometry`, logged | `test_null_geometry`, `test_null_and_empty_geometry_are_rejected_and_logged`, N-0001 |
| Empty geometry | rejected `empty_geometry` | E-0001, transformer test |
| Self-intersecting polygon | `make_valid(structure)`, flagged `geom_was_repaired` | `test_invalid_geometry`, H-0002 |
| Collapsed / zero-area polygon | rejected `invalid_geometry_unrepairable`, raw WKT kept | `test_invalid_geometry`, H-0003 |
| GeometryCollection | rejected `unsupported_geometry_type` | `test_unsupported_geometry_type_is_rejected` |
| Gauss-Krüger EPSG:31467 input | reprojected to 25832 within 2 mm of a pyproj reference | `test_wrong_crs_is_reprojected` |
| No CRS declared | every feature rejected `unknown_crs`; a CRS is never assumed | `test_missing_crs_is_rejected` |
| Unsupported CRS (EPSG:3857) | rejected `unknown_crs` | `test_unaccepted_crs_is_rejected` |
| lat/lon swapped in EPSG:4326 | lands outside Hesse and is rejected `out_of_region` | `test_swapped_axis_wgs84_is_caught_as_out_of_region` |
| Feature outside Hesse | rejected `out_of_region` | O-0001 |
| Missing classification: null, absent field, `""`, `unbekannt`, `k.A.`, `n.b.`, `-`, NaN | NULL + `missing`; never a low class | `test_missing_classification`, 9 parametrised unit cases, V5 = 0 |
| Unknown classification value | kept verbatim, `unrecognised` | A-0002 `Klasse 7`, `test_classification_vocabulary_handling` |
| Source without a classification field | NULL + `missing` | live layers, `test_live_layer_without_classification_field_is_missing` |
| Unmapped category | rejected; never guessed | U-0001 |
| Missing / duplicate feature ID | rejected; the first duplicate is kept | `test_unmapped_category_missing_id_and_duplicate_id_are_rejected` |
| Umlauts / case in source values | folded (`Tagesöffnung` → `tagesoeffnung`) | S-0003 mapped to `shaft` |
| Paging | WFS `startIndex` over 3 pages; ArcGIS `resultOffset` past `maxRecordCount` (live: 5 pages of 1000) | `test_pagination`, `test_arcgis_rest_paging_and_metadata`, live 4691 |
| Server offers no JSON | falls back to GML 3.2 and parses it with pyogrio | `test_gml_fallback_when_json_not_offered` |
| HTTP 500 | run marked `failed` with the error; nothing loaded | `test_http_error_marks_run_failed_and_loads_nothing` |
| Layer missing from GetCapabilities | `LayerNotFoundError`; run marked `failed` ("discovery failed") | `test_missing_layer_fails_discovery_visibly` |
| Changed feature | updated exactly once; other rows untouched | `test_changed_feature_is_updated_once` |
| Two live layers sharing OBJECTIDs | a distinct `source` per layer prevents key collisions | the `source` values in V1 |

## 5. Acceptance criteria

| Criterion | Status | Evidence |
|---|---|---|
| **Correct region (Hesse)** | Met | All sources are Hesse-only (HLNUG, or synthetic Hesse coordinates). Every stored geometry is inside the Hesse bbox (V2 `outside_hesse_bbox = 0`). Out-of-region input is rejected (O-0001, swapped-axis test). |
| **Correct mine/hazard category** | Met with a limitation | The four required categories are produced by the WFS pipeline and covered by tests (`test_valid_features`, V1). Real data for them does not exist publicly (docs/source_discovery.md), so they are demonstrated on synthetic fixtures. Live HLNUG data is kept in its own categories (`sinkhole_subsidence`, `extraction_site`) and never labelled as mine rights or shafts. |
| **Every geometry traceable to its source** | Met | V4: 0 rows missing `source_url`, `layer_name`, `license`, `fetched_at` or a valid `last_run_id`. `source_url` is a per-feature URL (WFS `resourceId`, ArcGIS `where=OBJECTID=`). `source_crs`, `geom_was_repaired` and raw `attributes` are kept per row. |
| **Re-running is safe, no duplicates** | Met | V9: the latest run per source inserted 0 and updated 0. V3: no duplicate `(source, source_feature_id)`. A UNIQUE constraint enforces it. `test_duplicate_run_produces_no_changes` compares the full table (hashes, `last_changed_at`, `last_run_id`) before and after. |
| **Rejected records are visible** | Met | `iris_staging.mining_restrictions_rejected` holds 6 rows per fixture run with reason, detail, raw feature and WKT (V7). Counts are in `harvest_runs.rejected_count`, and each reject is logged at WARNING. |

Other requirements:

| Requirement | Evidence |
|---|---|
| Missing classification distinct from safe/low | V5 `missing_with_value = 0`; a DB CHECK makes it impossible; V6 shows `<missing>` separately |
| Geometry validated, `make_valid` or reject, EPSG:25832 | V2 all 0; DB CHECK `ST_IsValid AND NOT ST_IsEmpty`; column typed `geometry(Geometry,25832)` |
| Only `iris_staging` written | all tables, including `alembic_version`, are in `iris_staging` (`\dt iris_staging.*`) |
| GIST index | `mining_restrictions_geom_gist`; example 5 shows a Bitmap Index Scan for `ST_DWithin` |
| Parcel overlap / proximity SQL | examples 1 to 4 on real HVBG ALKIS parcels: `ST_Intersects` (parcel in EPSG:25832, and in EPSG:4258 transformed), `ST_DWithin` 250 m from a parcel / 1 km from a point |
| Per-run legal/access/currency/source URL | V8 (`harvest_runs`), plus an INFO log at run start |
| No secrets in the repo | `.env.example` and `config.example.yaml` have blank or no passwords; compose requires them from `.env` (`:?` guards) |
| Pinned dependencies | `requirements.txt` / `-dev.txt` (`==`), plus `requirements.lock` (37 packages, verified identical to the image) |

## 6. Known gaps

1. **No real data for the four required categories.** This is not fixable without a public source or an access agreement
   with HLNUG or the Bergaufsicht.
2. **License for `geologie/*`:** CC BY 4.0 is inferred from HLNUG's INSPIRE terms. Confirm with HLNUG before downstream use.
3. **Live IDs:** `OBJECTID` stability across HLNUG republishing is unverified.
4. **Disappeared features:** no deletion or `last_seen` tracking.
5. **Rejected log:** it is per run and grows by the same rejects on every run.
6. **Region check:** a Hesse bounding box, not the state polygon.
