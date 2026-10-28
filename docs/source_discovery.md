# Source discovery log (Phase 1)

Status: COMPLETE (Phase 1). Date of probes: 2026-10-27.

## 1. Endpoint given in the task

`https://gds.hessen.de/wfs/altlasten`, layer `he_bergbau_kavernen`

| Request | Result |
|---|---|
| `?service=WFS&version=2.0.0&request=GetCapabilities` | HTTP 404, `Content-Type: image/jpeg` (error image, not XML) |
| `?service=WFS&version=1.1.0&request=GetCapabilities` | HTTP 404, `Content-Type: image/jpeg` |
| `https://gds.hessen.de/` | HTTP 200, redirects to `INTERSHOP/web/WFS/HLBG-Geodaten-Site/...` |

Finding: `gds.hessen.de` is the HLBG (Hessische Verwaltung für Bodenmanagement und Geoinformation) Geodatenshop, an
Intershop e-commerce site. The `WFS` in its URL path is Intershop's "Web Front Store", not an OGC Web Feature Service.
The given endpoint and layer name are treated as placeholders.

## 2. Hessen geoportal catalogue

| Request | Result |
|---|---|
| `geoportal.hessen.de/mapbender/php/mod_callMetadata.php?searchText={Bergbau,Altbergbau,Altlasten,Kaverne}&searchResources=wfs&outputFormat=json` | HTTP 200, `nresults: 0` for every term |
| `geoportal.hessen.de/csw?...GetRecords...` | HTTP 403 |

## 3. Other hosts probed

| Host | Result |
|---|---|
| `geodienste.hlnug.de`, `geoportal.hlnug.de`, `www.geoportal.hlnug.de`, `altbergbau.hessen.de` | DNS / connection failure |
| `gruschu.hessen.de` | redirects to `umweltdaten.hessen.de/mapapps/.../wasser` (HLNUG map.apps viewer) |
| `bodenviewer.hessen.de` | redirects to `umweltdaten.hessen.de/mapapps/.../bodenviewer` |

Lead: `umweltdaten.hessen.de` hosts the HLNUG viewers; its backing services are the next thing to check.

## 4. Background (from web search)

- Contaminated-land data (Altflächendatei, § 8 HAltBodSchG) is held by HLNUG in FIS AG (ALTIS merged into FIS AG in 2007).
- Mine-working outlines are kept by the Bergaufsicht at RP Darmstadt / Gießen / Kassel (Bad Hersfeld), and in 2018 they were
  supplied as shapefiles rather than through a public service.

## 5. Datasets supplied by the user from geoportal.hessen.de search

| Link | What it is | Relevant? |
|---|---|---|
| layer 53913 | FNP Malsfeld, 31. Änderung "Sonderbaufläche Biogas ... nachwachsender Rohstoffe" (Gemeinde Malsfeld, WMS via gdi-nordosthessen.de) | No: municipal land-use plan; matched on "Rohstoffe" |
| layers 58871, 58872, 58875 | Bebauungspläne Bad Hersfeld, Johannesberg (WMS) | No: zoning plans |
| ISO 5375bdca-... | Historische Luftbilder Hessen (HVBG), DL-DE-Zero-2.0 | No |
| ISO adf1fd09-... | Überschwemmungsgebiete HQ100 (HLNUG), CC BY 4.0 | No (flood), but revealed the HLNUG host `geodienste-umwelt.hessen.de` |

## 6. HLNUG service host `geodienste-umwelt.hessen.de`

- `/` returns 403. `/inspire/` is a public directory listing of INSPIRE shapefile ZIPs (flood zones, IED installations,
  GÜK300 geology, soils). None of them cover mining, shafts, cavities or contaminated land.
- `/arcgis/rest/services?f=json` is ArcGIS Server 11.3 with public folders: agrar, arten, boden, geologie, haa, inspire, ...
- None of the checked services has the WFS extension enabled (`supportedExtensions` is empty, `.../MapServer/WFSServer`
  returns HTTP 400). Features can only be retrieved through the ArcGIS REST `query` operation (capabilities `Map,Query,Data`).
- CRS: native EPSG:25832. Copyright: "© Hessisches Landesamt für Naturschutz, Umwelt und Geologie (HLNUG)".

| Service | Content | Fit to TASK 090 |
|---|---|---|
| `geologie/geogefahren` | Geologie-Viewer Geogefahren: earthquakes, landslides (Rutschungsdatenbank), rockfall, settlement-sensitive layers, karst, **Erdfälle und Senkungsmulden** (layer 20), InSAR ground motion | Partial: natural subsidence/sinkholes, not mining |
| `geologie/rohstoffe` | Raw materials: **Abbaustellen** (extraction sites) by commodity, Lagerstättenpotenzial, Rohstoffsicherungsflächen KRS | Partial: surface quarries/pits, not mine rights or shafts |
| `haa/haa` | Unknown | HTTP 403: access restricted, not pursued |

Not found in any public service: Bergbauberechtigungen (mine rights), Schächte/Stollen (shafts/adits), Hohlräume/Kavernen
(cavities/caverns), Altflächendatei (contaminated land). Consistent with section 4: mining data sits with the Bergaufsicht
(RP), and the Altflächendatei (FIS AG) is not published as open feature data.

## 7. Live layer schemas (REST equivalent of DescribeFeatureType)

Base: `https://geodienste-umwelt.hessen.de/arcgis/rest/services/geologie`. Paged `query`, `maxRecordCount` 1000.

| Layer | Geometry | Count (2026-10-27) | Fields |
|---|---|---|---|
| `geogefahren/MapServer/20` Erdfälle und Senkungsmulden | Point | 4691 | OBJECTID, AMT_NR (int), TS_FLAE (double) |
| `rohstoffe/MapServer/1` Abbaustellen | Point | 276 | OBJECTID, KRS_NR, FIRMA, GEWINNUNGS, ABBAURECHT, ROHSTOFF, ROHSTOFFGR, ROHSTOFFEI, AUFSCHLUSS, ABBAUART, STADT_GEME, GEMARKUNG_, FOERDERUNG |

Neither layer carries a published hazard classification, so live rows load with `classification_status = 'missing'`.
That makes them a real-data check of the "never default to safe" rule.
`ABBAURECHT` (extraction right) is the closest live field to a mine right.
Do not use OBJECTID as a stable feature ID until it has been confirmed stable across runs. Prefer `AMT_NR` / `KRS_NR`
where they are populated and unique.

License / access:
- `HLNUG_INSPIRE_Nutzungsbedingungen.txt` (Stand Mai 2025): no restrictions on public access; free use for private and
  commercial purposes; CC BY 4.0; not legally binding information.
- Service copyright text: "© Hessisches Landesamt für Naturschutz, Umwelt und Geologie (HLNUG)".
- Assumption (unverified): the `geologie/*` services fall under the same CC BY 4.0 terms. Recorded per run as
  `license = 'CC-BY-4.0 (assumed, per HLNUG INSPIRE terms)'` until confirmed.

## 8. Outcome (decided 2026-10-27)

Outcome B, plus a live check:

1. **Primary: fixture-based WFS demonstrator.** OGC WFS 2.0 client exercised against mocked GetCapabilities,
   DescribeFeatureType and GetFeature responses. Synthetic HLNUG-shaped features cover the four required categories
   (mine_right, shaft, cavity, contaminated_land). The fixtures are marked synthetic and loaded with mode `fixture`.
2. **Secondary: live ArcGIS REST adapter** for the two HLNUG layers above, mode `live`. It shows real provenance,
   EPSG:25832 handling, paging and idempotent re-runs on real Hesse data. These layers are geohazard (sinkholes) and
   extraction sites, **not** the required mine-right/shaft/cavity/contaminated-land categories. They are mapped to
   their own categories (`sinkhole_subsidence`, `extraction_site`) so they are never mislabelled.
3. Not used: `haa/haa` (HTTP 403, restricted), the Altflächendatei / FIS AG (not public), and the Bergaufsicht mining
   data (no public service). No access restriction was bypassed.
