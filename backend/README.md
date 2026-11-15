# Backend: the `analog` pipeline and the Django API

This folder holds the Python side of [Earth Analog Explorer](../README.md):

- **`src/analog/`, the pipeline and its `analog` command:** downloads or imports Earth data, computes per-cell features, scores every cell against Moon and Mars targets, trains the classifier and exports JSON for the web app. Runs on your computer (it needs GDAL/rasterio and can use gigabytes of data).
- **`server/` + `api/`, a Django API:** serves the same results, re-scores a region with custom weights, stores team notes, and offers an admin to edit targets and known analogs. Optional: the web app works without it.

The goal, method, formulas and results are in the [project README](../README.md).

## Contents

1. [Setup](#1-setup)
2. [The CLI: data, training and export](#2-the-cli-data-training-and-export)
3. [Django API](#3-django-api)
4. [Tests](#4-tests)
5. [Configuration and environment variables](#5-configuration-and-environment-variables)
6. [Troubleshooting](#6-troubleshooting)
7. [Deployment](#7-deployment)
8. [Future work](#8-future-work)

---

## 1. Setup

Python 3.10+ (tested with 3.12). For downloads, a free NASA Earthdata account: <https://urs.earthdata.nasa.gov/users/new>.

```bash
# from the repository root
python -m venv .venv
.venv\Scripts\activate                       # Windows (PowerShell / cmd)
# source .venv/bin/activate                  # macOS / Linux
pip install -e "backend[dev]"                # the `analog` CLI + test tools
pip install -r backend/requirements.txt      # the Django API (only if you run it)
python -c "import earthaccess; earthaccess.login(persist=True)"   # save your Earthdata login once
```

```text
backend/
├── pyproject.toml            # `analog` package + CLI; pytest config
├── requirements.txt          # Django API dependencies (what the Cloud Run image installs)
├── config/                   # targets, mineral groups, CRISM thresholds, download options
├── src/analog/               # the pipeline
├── gee/analog_features.js    # Google Earth Engine script: features for any site, or all land
├── server/                   # Django settings (from environment variables), urls, wsgi
├── api/                      # Django app: models, admin, views, scoring (reuses src/analog)
├── tests/                    # pytest: pipeline, API, known-analog files
├── manage.py
├── Dockerfile, Dockerfile.dockerignore, entrypoint.sh, cloudbuild.yaml, .gcloudignore
└── .env.example
```

## 2. The CLI: data, training and export

Run `analog --help` or `analog <command> --help` for every option. All commands work from any folder.

### 2.1 The workflow

| Step | Command | Result |
|---|---|---|
| 1. Define an area | `analog region create ID --bbox W S E N` | `data/regions/ID/` with a grid of ~1 km cells |
| 2. Get data | `analog sync ID` (download), or `analog import FILES` (data you already have) | layers: elevation, albedo, NDVI, water, mineral maps |
| 3. Compute features | `analog features ID` | `features.parquet`: one row per cell |
| 4. (Optional) train | `analog train --regions ...` | `data/models/classifier.joblib` |
| 5. Score and export | `analog export` | `data/export/*.json`, copied to `frontend/public/data/` |

`analog run ID` does steps 3 and 5. `sync` and `import` take `--run` to do them right after the data arrives. After an export, click **Reload data** in the web app.

### 2.2 Try it without downloads: `analog demo`

Builds a **synthetic** region, `demo-synthetic`, in the open ocean at 0–0.6°N, 0–0.6°E, so it can never be mistaken for a real place. It has a flat plain, a rugged massif, a crater and a sulfate-rich basin. The command then computes features, scores and exports.

`analog demo --train` also trains the classifier on synthetic labels. That **overwrites** `data/models/classifier.joblib`, which then applies to every region until you retrain on real labels.

> The demo writes into your real project: a `demo-synthetic` region and 4 fake known analogs in `data/labels/demo_labels.geojson`, which then appear in the app and in the 50 km exclusion. The project's data no longer includes them. If you run the demo, remove it afterwards: delete `data/regions/demo-synthetic/` and `data/labels/demo_labels.geojson`, then run `analog export` (it also removes the region's stale export files).

### 2.3 Regions

```bash
analog region create atacama-yungay --bbox -70.0 -24.6 -69.4 -24.0 --title "Atacama Desert - Yungay"
analog region list              # regions, whether features exist, their layers
analog region show atacama-yungay
```

- **IDs:** lowercase letters, digits, `-` and `_`.
- **Cells:** 0.01° by default (about 1 km); change with `--cell`.
- **Size:** keep regions about **1°×1° or smaller**; the grid is limited to 2,000,000 cells.
- **Placement matters:** known analog sites and everything within **50 km** of them are never reported as candidates. A region drawn around a known site mostly serves training and validation; **discovery regions** away from them produce the new candidates.

### 2.4 Adding data

All routes go through the same processing code, so a file downloaded by the CLI and the same file imported by hand give identical results.

#### Download from NASA: `sync` and `fetch`

```bash
analog sync atacama-yungay            # 1) imports matching files from data/inbox/  2) downloads what's still missing
analog sync --all --run               # every region, then features + export
analog sync ID --offline              # only use local files, never download
analog sync ID --force                # redo sources the region already has
```

`sync` reads per-source and per-region options (dates, cloud limit, scenes per tile) from [`backend/config/sources.yaml`](config/sources.yaml). To fetch one source yourself:

| Command | Downloads | Creates layers | Options |
|---|---|---|---|
| `analog fetch srtm ID` | SRTM 30 m elevation tiles | `dem` | |
| `analog fetch hls ID` | Harmonized Landsat-Sentinel scenes | `albedo`, `ndvi`, `water`, `clay_index`, `ferric_index` | `--start`, `--end`, `--max-cloud` (10), `--scenes-per-tile` (2) |
| `analog fetch emit ID` | EMIT L2B mineral maps (arid regions only) | `mineral:<group>` | `--start`, `--end`, `--max-granules` (5) |
| `analog fetch meteomatics ID` | Meteomatics daily climate data (one year) | 9 climate features (see below) | `--year`, `--res`, `--offline` |

Re-fetching a source **replaces** that source's layers.

#### Files you already have: `import`

For hackathon-provided data, manual downloads, a shared drive or a USB stick. `import` accepts **files, folders, globs, `.zip` files and http(s) URLs**, recognises each file by its **original NASA file name**, and sends it to **every region it overlaps**.

```bash
analog import D:/hackathon-data/ --dry-run                 # show what's recognised and where it would go
analog import D:/hackathon-data/ --run                     # import, then features + export
analog import srtm_tiles.zip --region usa-mdrs             # only this region (repeatable)
analog import my_copernicus_dem.tif --kind dem --region canada-mistastin
analog import "https://data.lpdaac.earthdatacloud.nasa.gov/.../EMIT_L2B_MIN_....nc"
analog import data/regions/usa-mdrs/raw/hls --region usa-mdrs   # re-process files already downloaded
```

| Type | Recognised file names | Becomes |
|---|---|---|
| SRTM | `N24W070.hgt`, `N24W070.SRTMGL1.hgt.zip` | the `dem` layer (tiles mosaicked) |
| HLS | `HLS.L30.<tile>.<date>.v2.0.<band>.tif` and `HLS.S30...`, one file per band | albedo, NDVI, water, clay and ferric indices. Each scene needs blue, red, NIR, SWIR1, SWIR2 and Fmask; incomplete scenes are skipped with a message |
| EMIT | `EMIT_L2B_MIN_<ver>_<datetime>_....nc` | `mineral:<group>` layers (uncertainty files are ignored) |
| CRISM | `<obs>_07_su<nnn>j_mtr3.img` + `.lbl` | a **Mars target's** mineral values (use `--target`) |
| Earth Engine features | `analogfeat_<site>.tif` (from `backend/gee/analog_features.js`) | 12 features at once (terrain, albedo, NDVI, water, temperature range, clay and ferric indices, gentle-slope share) |
| Any other raster | GeoTIFF, `.img`, `.vrt`, `.jp2`... | a region layer. The kind is guessed from the name (`dem`, `dtm`, `srtm`, `copernicus`, `lola`, `mola` → `dem`; `ndvi`; `albedo`; `water`; `temp_range`) or given with `--kind` |

Good to know:
- **Keep the original file names.** Renamed NASA files aren't recognised.
- **No region overlaps?** `import` prints the `analog region create` command with the files' bounds.
- **Zips** are extracted to `data/inbox/.extracted/`. **URLs** go to `data/downloads/`. Earthdata URLs use your login; others (e.g. the PDS archive) are fetched anonymously.
- Importing HLS or EMIT **replaces** that source's layers in the region; `--append` adds instead.

#### Climate data: Meteomatics

Space Apps participants get free access to the [Meteomatics Weather API](https://www.meteomatics.com/en/meteomatics-and-nasa-space-apps-challenge/) **for the duration of the challenge only**, so download what you need while the account works. The saved files keep working afterwards.

```bash
analog fetch meteomatics atacama-yungay          # one year (2024) of daily data -> 9 climate features
analog fetch meteomatics atacama-yungay --offline   # rebuild from the saved responses, no request
```

- **Credentials:** `METEOMATICS_USERNAME` and `METEOMATICS_PASSWORD`, or `meteomatics/credentials.md` with `Username: ...` and `Password: ...` lines. That folder is git-ignored. Never commit it.
- **Requests:** 12 per region (one per month: a ~0.05° grid, every day, 9 parameters). The account allows 1,000 a day. Responses are saved in `data/regions/<id>/raw/meteomatics/`, and later runs reuse them.
- **Options:** `--year` (default 2024), `--res` (grid spacing in degrees, default 0.05).

| Feature (team schema name) | Criterion | Computed as |
|---|---|---|
| `tmin_k` | 3 Thermal | lowest daily minimum air temperature at 2 m (K) |
| `tday_range_k` | 3 Thermal | mean daily range of 2 m air temperature (K) |
| `tyear_range_k` | 3 Thermal | warmest minus coldest monthly mean (K) |
| `sun_elev_deg` | 3 Thermal | mean noon sun elevation over the year, from latitude (no request) |
| `hydrogen_or_soilmoist` | 4 Water & ice | mean soil moisture index at 5 cm |
| `ice_flag` | 4 Water & ice | 1 if snow or frozen ground on ≥ 15 days a year |
| `dust_aod` | 5 Surface processes | mean dust aerosol optical depth at 550 nm |
| `wind_ms` | 5 Surface processes | mean daily wind speed at 10 m (m/s) |
| `aridity_index` | 6 Biology (dossier only) | yearly precipitation ÷ evapotranspiration |

**Where these features count:** they appear in the app and the classifier uses them. The similarity score uses them only once a target has a Moon/Mars value for them; none does yet. Train without them with `analog train ... --skip-meteomatics`.

**Air vs. surface temperature:** Meteomatics temperatures are air at 2 m, while Moon/Mars thermal values describe the surface. The Earth Engine `temp_range` (MODIS surface temperature) is the closer match for criterion 3.

#### The inbox

Drop files anywhere under `data/inbox/` and run `analog sync ID` (or `--all`): files that match the region are imported first, and only what's still missing is downloaded. The inbox folder can be changed in `backend/config/sources.yaml`.

#### Your own rasters: `ingest`

For a GeoTIFF of a known kind, added to one region:

```bash
analog ingest ID dem.tif --kind dem
analog ingest ID sulfate_map.tif --kind mineral:sulfate
analog ingest ID dust.tif --kind custom:dust_index      # a brand-new feature
analog run ID
```

| `--kind` | Expects | Becomes feature |
|---|---|---|
| `dem` | elevation in metres | slope, steep slope, roughness, relief, elevation |
| `albedo` | 0–1 | `albedo` |
| `ndvi` | −1…1 | `ndvi` |
| `temp_range` | kelvin | `temp_range` |
| `water` | 1 = water, 0 = land | `water_fraction` |
| `clay_index`, `ferric_index` | HLS band ratios | same name |
| `mineral:<group>` | 0/1 or a share | `min_<group>` |
| `custom:<name>` | anything | `<name>` (averaged per cell) |

`ingest` **adds** to existing layers. `--link` references the file instead of copying it. A custom feature is only scored once a target lists it (see 2.5).

#### Any place on Earth, without downloads: Google Earth Engine

[`backend/gee/analog_features.js`](gee/analog_features.js) computes the features on Google's servers from global datasets, so a new area needs no imagery download. Use it for **discovery regions** far from the known analogs.

1. In the script, list your sites (`SITES = [{id: 'namib-sossusvlei', bbox: [15.0, -25.0, 15.6, -24.4]}]`, ~1 km cells), or leave the list empty for all land between 56° S and 60° N on a ~5 km grid (0.05°), exported as one file per continent (`analogfeat_earth_europe.tif`, ...).
2. Paste it into the [Earth Engine Code Editor](https://code.earthengine.google.com) (needs a free non-commercial Earth Engine account), click **Run**, then **RUN** each export in the **Tasks** tab. The files arrive in Google Drive as `analogfeat_<site>.tif`.
3. On your computer:

   ```bash
   # 1. put the downloaded .tif files in data/inbox/gee/ (git-ignored)
   # 2. create the region(s) they should fill (import only fills regions that already exist)
   analog region create namib-sossusvlei --bbox 15.0 -25.0 15.6 -24.4   # a site: same id and bbox as in SITES
   # 3. import: crops each file to every region it overlaps, splits the bands into features, then --run
   #    computes features, scores and the export
   analog import data/inbox/gee --run
   ```

   **Whole-Earth (continent) files:** create **discovery regions inside a continent** with `--cell 0.05` and corners on multiples of 0.05°, so the cells line up with the export's grid, e.g.

   ```bash
   analog region create eu-iberia-interior --bbox -7 37 -2 42 --cell 0.05 --title "Iberian interior (Earth Engine)"
   analog import data/inbox/gee --dry-run      # shows which file goes to which region
   analog import data/inbox/gee --run
   ```

   A 5° × 5° box is 10,000 cells, a size the web app handles well. A whole continent works too (`--bbox -25 35 45 60 --cell 0.05` for Europe), but that is ~700,000 cells: fine for the pipeline, too heavy for the web app's region file.

   **Or let the pipeline make the regions: the worldwide screen.**

   ```bash
   analog world import data/inbox/gee          # every continent file -> 5 deg tiles with land, features computed
   analog world list                           # the tiles (world-n35w010 = south-west corner 35 N, 10 W)
   analog export                               # scores them; candidates go to the app's "Worldwide" list
   ```

   Each continent file is cut into 5° tiles on its own grid (`--tile`), tiles with less than 5% land are skipped (`--min-land`), and each tile becomes a region with scope `world`. Europe gives 56 tiles (560,000 cells) in ~40 s. The export scores every tile like any region but keeps their candidates in a separate list (`candidates_world`, best 25 per target, 15 km apart across tiles), and publishes the cell file only of tiles holding one of those candidates (17 for Europe), so the site stays small. In the app, tick **Worldwide** to switch the candidate list and region menu to them. Re-running `world import` with new continent files adds tiles; existing ones are refilled.

Each file has 12 bands, which become features with the pipeline's own names:

| Features | Source (Earth Engine catalog) |
|---|---|
| elevation, mean and 75th-percentile slope, roughness, relief | NASADEM 30 m (same definitions as with SRTM) |
| albedo | MODIS MCD43A3, median |
| NDVI | MODIS MOD13A1, median |
| water fraction | JRC Global Surface Water (water ≥ 50% of the time) |
| **temperature range** (day − night) | MODIS MOD11A2: gives criterion 3 its first Earth data |
| clay and ferric indices | MODIS MCD43A4 reflectance ratios |
| share of land at 0–15° slope (criterion 8) | NASADEM 30 m slope, JRC water masked |

**Things to know:**
- **No minerals:** EMIT only covers arid land, so there are no EMIT minerals here. Use the 1 km pipeline (`sync`) where minerals matter.
- **Coarser surface data:** the surface bands come from 500 m–1 km MODIS instead of 30 m HLS.
- **Mixing sources:** in a region that also has SRTM or HLS data, those values win, and Earth Engine fills only the gaps.
- **Re-importing** replaces the region's previous Earth Engine layers.
- **Whole-Earth runs:** the app can't yet show them as one map, which is the next step.

#### Cheat sheet

| You have | Do this | Then |
|---|---|---|
| A new Earth area | `analog region create` + `analog sync ID` | `analog run ID` |
| A folder of NASA files | `analog import FOLDER --dry-run`, then without `--dry-run` | `analog run --all` |
| A new area, without downloading imagery | Earth Engine script → `analog region create` + `analog import analogfeat_*.tif` | `--run`, or `analog run ID` |
| A GeoTIFF of a known kind | `analog ingest ID file.tif --kind ...` | `analog run ID` |
| A new kind of measurement | `--kind custom:<name>`, then add `<name>:` to a target's features | `analog run ID` |
| A Moon/Mars DEM clip | `analog target from-dem ...` or `analog import clip.tif --target ...` | `analog export` |
| CRISM mineral maps | `analog import FOLDER --target mars_jezero` | `analog export` |
| Better known-analog outlines | Edit `data/labels/known_analogs.geojson` | `analog train ...`, then `analog export` |
| Different EMIT mineral grouping | Edit `backend/config/mineral_groups.yaml` | `analog fetch emit ID`, then `analog run ID` |

### 2.5 Moon and Mars targets

A **target** is the numeric profile of a Moon or Mars site that Earth cells are compared with.

- **Built-in targets:** [`backend/config/targets.yaml`](config/targets.yaml).
- **Your additions and overrides:** `backend/config/targets.d/*.yaml`. These load after the built-in file, and the same ID replaces it.
- **List them:** `analog target list`.

```yaml
targets:
  my_target_id:
    name: Human-readable name
    body: moon | mars | earth
    category: free text           # shown in the app
    mission: habitat | landing    # groups targets in the app: a human base, or a landing site
    description: free text
    features:
      slope_mean: {value: 14, sigma: 6, weight: 1.0}                       # mode "near" (default)
      ndvi:       {value: 0.1, sigma: 0.06, weight: 1.5, mode: atmost, required: true}
      temp_range: {value: 25, sigma: 10, weight: 0.3, mode: atleast}
      tmin_k:     {percentile: 0.02, weight: 0.5}                          # percentile feature (below)
```

What each field does:
- **`value`, `sigma`:** the target value and its tolerance. One sigma off scores 0.61 for that feature, two sigma 0.14.
- **`weight`:** how much the feature counts.
- **`mode`:** `near`, `atleast` (anything above is perfect) or `atmost` (anything below is perfect).
- **`mission`** (target level, optional): `habitat` (where a crew could live) or `landing` (where a spacecraft could land). The app groups targets by it; targets without one are grouped by body.
- **`required: true`:** cells without the feature get no score. NDVI is required everywhere, so vegetated land can't pass as lunar.
- **`criterion: N`:** moves a feature to another of the 8 criteria.
- **`percentile` (instead of `value`):** for features Earth can't match in absolute terms (temperature, hydrogen, dust, wind). Where the site ranks **on its own world**, 0–1: `tmin_k: {percentile: 0.02}` = "among the coldest 2% of the Moon". A cell is compared by its rank **on Earth's land**; `sigma` (default 1) is in normal-score units, so the 2nd vs. the 16th percentile scores 0.61. `mode` works as usual (`atmost`: as cold or colder). Needs the Earth reference (below).

**Earth reference for percentile features.** `config/earth_reference.json` holds, for each feature, 1001 quantiles of its values over Earth's land (56° S–72° N, area-weighted). Build it once:

```bash
analog earth-ref build                       # Meteomatics, global 5 deg grid, 2024, 4 days a month: 12 requests, ~10 min
analog earth-ref build --offline             # rebuild from the saved responses (data/earth_reference/)
analog earth-ref build --source regions --only temp_range --merge   # add features from your regions' cells,
                                             # e.g. after importing the Earth Engine whole-land export
analog earth-ref show                        # p5 / p25 / p50 / p75 / p95 per feature
analog export                                # rescore with it
```

Without a reference, percentile features are skipped with a warning. The scores use the column `rel__<feature>` (Φ⁻¹ of the cell's Earth rank), which the export also writes, so the app shows "p3 on Earth" next to the value.

> The built-in values are **rough starting estimates**. Replace the terrain numbers with values measured from real DEMs before presenting.

**Measure a target from a Moon or Mars DEM** (e.g. a GeoTIFF clip from Moon/Mars Trek around a landing site):

```bash
analog target from-dem moon_my_site --dem lola_clip.tif --body moon --base moon_malapert_massif
# same thing through import:
analog import lola_clip.tif --target moon_my_site --body moon --base moon_malapert_massif
```

The DEM is cut into blocks of about 1 km (`--cell-m`). Each block's slope, steep slope, roughness and relief use the same definitions as for Earth cells. The block mean becomes `value` and the spread between blocks becomes `sigma`. The result goes to `backend/config/targets.d/<id>.yaml`. `--base` copies the non-terrain features (minerals, albedo, NDVI) from an existing target. If the DEM's projection isn't recognised, give `--pixel-m`.

**Add measured Mars minerals from CRISM:** `analog import data/inbox/crism --target mars_jezero`.

Targets can also be edited in the backend's admin (see 3.3); the backend then uses those versions.

### 2.6 Features and scoring

```bash
analog features ID [ID...] | --all      # compute data/regions/ID/features.parquet and print a summary
analog run ID [ID...] | --all           # features, then export
```

Per cell, the pipeline computes:
- **Terrain:** mean and 75th-percentile slope, roughness, relief, elevation.
- **Surface:** albedo, NDVI, water fraction, and clay and ferric indices from HLS.
- **Minerals:** EMIT mineral-group shares (`min_*`).

Scoring then gives each cell:
- **An overall similarity** (0–1) to every target.
- **A score per criterion** (geology, terrain, operations...): **≥ 0.70** counts as met.
- **A tier** from the share of criteria met: **A ≥ 0.75, B ≥ 0.50, C ≥ 0.25, D = at least one met.** A cell must pass the target's bare-ground (NDVI) limit to get a tier.

Formulas and definitions: [project README, section 4](../README.md#4-method-and-formulas).

### 2.7 Training the classifier

The classifier learns what known analog sites look like and scores every cell's probability of being a similar analog. It is optional: without it, the app shows only the similarity scores.

1. **Label known sites** in [`data/labels/known_analogs.geojson`](../data/labels/known_analogs.geojson). Each site is a polygon with properties `name`, `analog_type` (e.g. `basaltic_volcanic`, `impact_crater`), `analog_for` (Moon or Mars) and `criteria_known_for` (e.g. `[1, 2]`). Keep [`data/known_analogs.csv`](../data/known_analogs.csv), the team's list, in step: same sites and fields (`backend/tests/test_labels.py` checks it). One model is trained per `analog_type`. A type can only be **tested** with at least two sites inside your regions; aim for 3–5.
2. **Cover them with regions,** plus some background regions, and get their features (2.3–2.6).
3. **Train:**

   ```bash
   analog train --regions atacama-yungay usa-mdrs usa-meteor-crater usa-kau-desert canada-mistastin eu-rio-tinto eu-lanzarote haiti-northwest
   analog export                       # apply the model to every region
   ```

   | Option | Default | Meaning |
   |---|---|---|
   | `--regions ID...` / `--all` | | Regions to learn from (`--all` includes the synthetic demo region if it exists) |
   | `--labels FILE` | `data/labels/known_analogs.geojson` | Labelled sites |
   | `--bags N` | 10 | Random-forest bags per type (more = steadier, slower) |
   | `--seed N` | 0 | Random seed |
   | `--skip-meteomatics` | off | Train without the 9 Meteomatics climate features |
   | `--exclude FEATURE...` | none | Leave any features out of training (e.g. `--exclude dust_aod wind_ms`) |

   **Use `--skip-meteomatics` (the model shipped in the app is trained this way).** With the climate features, both volcanic sites fail the top-10% test (Kaʻū AUC 0.89 → 0.69, Lanzarote 0.89 → 0.78), because climate tells the model *where* a site is (latitude, ocean, altitude) rather than *what* it is, the same reason elevation is excluded. The climate features belong in the similarity score, against Moon/Mars values.

4. **Read the validation it prints.** Each site of a type is hidden in turn, the model is retrained without it, and the run reports how well the hidden site is found:
   - **`auc`:** how well the site's cells outrank background (0.5 = chance, 1 = perfect).
   - **`auc_bare`:** the same, against bare ground only. This is the honest test.
   - **`beats_background_pct`:** the share of background cells the site beats.
   - **`top10`:** passes when the hidden site ranks in the **top 10%**, the project brief's success test.

The model is saved to `data/models/classifier.joblib`. Delete that file to turn the classifier off.

### 2.8 Export

```bash
analog export                 # re-score every region and write data/export/
analog export --no-rescore    # reuse existing scores.parquet
```

| Writes | Contents |
|---|---|
| `data/export/index.json` | Targets, feature catalogue, regions, candidates per target, criteria and tier definitions, known analogs, classifier validation |
| `data/export/regions/<id>.json` | Every cell of the region: coordinates, features, scores, distance to the nearest known analog |

How candidates are picked, per target, in each region:
1. Cells **within 50 km of a known analog are excluded**: known sites test the method, they aren't results.
2. A cell must score **≥ 0.2** and meet **at least one criterion**.
3. Cells are ranked by **tier, then score**.
4. Candidates are kept at least **15 km apart**.
5. The best **25** across all regions are listed.

The export is **copied to `frontend/public/data/`** automatically. Commit that folder to update the deployed site.

### 2.9 Command reference

| Command | What it does | Main options |
|---|---|---|
| `analog demo` | Synthetic region → features → export | `--train` |
| `analog region create ID --bbox W S E N` | Defines a region | `--cell`, `--title` |
| `analog region list` / `show ID` | Lists regions / shows one region's grid and layers | |
| `analog sync ID... \| --all` | Inbox, then downloads what's missing | `--force`, `--offline`, `--run` |
| `analog fetch srtm\|hls\|emit\|meteomatics ID` | Downloads one source | `--start`, `--end`, `--max-cloud`, `--scenes-per-tile`, `--max-granules`; Meteomatics: `--year`, `--res`, `--offline` |
| `analog import SOURCE...` | Imports files, folders, globs, zips or URLs | `--region`, `--kind`, `--target`, `--body`, `--base`, `--pixel-m`, `--scenes-per-tile`, `--append`, `--dry-run`, `--run` |
| `analog ingest ID FILE... --kind KIND` | Adds your own raster(s) to a region | `--link` |
| `analog features ID... \| --all` | Computes the feature table | |
| `analog target list` | Lists targets and their source files | |
| `analog target from-dem ID --dem F --body B` | Measures a target's terrain from a DEM | `--base`, `--name`, `--category`, `--description`, `--cell-m`, `--pixel-m`, `--lat`, `--lon` |
| `analog world import FOLDER\|FILES` / `world list` | Earth Engine continent files → 5° world tiles (automatic regions), features | `--tile`, `--min-land`, `--no-features`, `--run` |
| `analog earth-ref build` / `show` | Earth land quantiles for percentile target features | `--source meteomatics\|regions`, `--year`, `--res`, `--offline`, `--regions`, `--only`, `--merge` |
| `analog train` | Trains the classifier and prints validation | `--regions` / `--all`, `--labels`, `--bags`, `--seed` |
| `analog export` | Scores every region, writes and publishes the JSON | `--no-rescore` |
| `analog run ID... \| --all` | `features`, then `export` | |

### 2.10 Logs

Every `analog` command shows its progress on screen and writes a full log to **`data/logs/<date>-<time>_<command>.log`** (e.g. `20261114-142953_train.log`). The path is printed at the end of each run.

| Command | Progress shown | Extra detail in the log file |
|---|---|---|
| `train` | Each region loaded (cells, cells inside known sites); per analog type its cells, sites and features; bag fitting time; each leave-one-site-out result with AUC and **top-10% pass/fail**; model size and total time | Every bag (cells used, seconds) |
| `export` | Each region `[k/8]`: cells, cells excluded near known analogs, candidates, seconds; candidates per target with their tiers; total size and time | Per region: similarity and classifier time; per target, cells scored and how many cells are in tiers A–D |
| `features`, `run` | Each layer turned into a feature: files, share of cells with data, seconds | |
| All commands | Everything else they print | The exact command line, Python version, how long it took, and the error (or full traceback) when it fails |

The newest 100 log files are kept. `data/logs/` is git-ignored. Set `ANALOG_LOG_DIR` to write logs elsewhere, or `ANALOG_NO_LOG_FILE=1` to skip the file.

## 3. Django API

[`backend/`]() holds a small **Django 5 + Django REST Framework** API next to the pipeline. The web app works without it. With it, you also get:

- **Weight sliders** in the app: the backend re-scores a region with your weights, using the pipeline's own `similarity.py` and `criteria.py` (identical results at default weights; a test checks it).
- **Team notes** on any cell, with an optional verdict (good analog / not an analog / unsure), protected by a shared team token. Field reviews (data matches / wrong per criterion, reliability 1–5) need no backend: see [frontend/README.md](../frontend/README.md).
- **Django admin** to edit targets and known analogs in the browser instead of YAML.

Everything is served under `/api/` (API, admin, static files), so in production one rewrite connects it to the site.

### 3.1 Run it locally

```bash
cd backend
python manage.py migrate
python manage.py load_export          # copy targets + known analogs from data/export into the database
python manage.py createsuperuser      # an account for /api/admin/
python manage.py runserver            # http://127.0.0.1:8000/api/health
```

Locally it uses SQLite (`backend/db.sqlite3`), reads `data/export/`, and the team token is `dev-token`. `load_export --only-new` keeps admin edits and only adds new rows.

### 3.2 Endpoints

| Endpoint | Returns |
|---|---|
| `GET /api/health` | Status of the export and the database |
| `GET /api/index` | `index.json`, with targets and known analogs from the database (if loaded) |
| `GET /api/regions/<id>` | `regions/<id>.json` |
| `GET /api/score?target=ID&region=ID&w.<feature>=<weight>` | One region re-scored for one target with custom weights (0–10; 0 ignores a feature): scores, criteria, tiers and candidates |
| `GET /api/notes?region=ID&cell=N` | Team notes, newest first |
| `POST /api/notes` | Adds a note: JSON `{region, cell, lat, lon, author, verdict, text, target_id}`, header `X-Team-Token`. `verdict` is `good`, `bad`, `unsure` or empty; a note needs a verdict or a text |
| `GET /api/notes/summary` | Verdict counts per cell: `{"<region>:<cell>": {"good": n, "bad": n, "unsure": n}}` |
| `/api/admin/` | Django admin |

### 3.3 Admin

`/api/admin/` lists three tables:

| Table | What editing it does |
|---|---|
| **Targets** | Mission (habitat / landing) and feature profiles as JSON (same format as the YAML in 3.5, validated on save). Edits show up in `/api/index` and are used by `/api/score`. The CLI's `analog export` still uses the YAML files. |
| **Known analogs** | Bounding boxes used for the 50 km exclusion in `/api/score` |
| **Candidate notes** | The team's notes and verdicts |

### 3.4 How it's built

| Path | Contents |
|---|---|
| `server/settings.py` | All settings from environment variables (see section 5) |
| `api/models.py` | `Target`, `KnownAnalog`, `CandidateNote` |
| `api/data.py` | Reads the exported JSON, cached and reloaded when the files change |
| `api/scoring.py` | Re-scoring with custom weights (imports `analog.similarity` and `analog.criteria`, which need only numpy and pandas) |
| `api/views.py`, `api/urls.py` | The endpoints |
| `api/management/commands/load_export.py` | `manage.py load_export` |
| `Dockerfile`, `entrypoint.sh` | Container for Cloud Run: migrates, loads the export once, runs gunicorn |

The container image doesn't need rasterio or GDAL. It contains `backend/` and the exported data (`frontend/public/data`), so a new export means rebuilding the image (section 7).

## 4. Tests

```bash
cd backend && pytest -q        # pipeline + API + known-analog files (19 tests, about 1.5 minutes)
cd backend && ruff check .     # lint (rules in pyproject.toml)
```

Both run on every push in GitHub Actions ([.github/workflows/ci.yml](../.github/workflows/ci.yml)), with the frontend's typecheck and tests.

| Suite | Covers |
|---|---|
| [`tests/test_pipeline.py`](tests/test_pipeline.py) | Runs in a temporary project. Similarity maths; the full demo pipeline with the classifier; custom-feature ingest and `target from-dem`; file-name detection; importing a GeoTIFF and a planetary DEM; Earth Engine imports (both band versions); Meteomatics statistics from cached responses; training with excluded features |
| [`tests/test_api.py`](tests/test_api.py) | A small fixture export: health; index (export vs. database); region IDs (including path tricks); scores with tiers, NDVI gate and 50 km exclusion; custom weights; bad requests; notes needing the team token; verdicts and their summary. Also: `/api/score` at default weights reproduces the real export |
| [`tests/test_labels.py`](tests/test_labels.py) | `data/known_analogs.csv` and `data/labels/known_analogs.geojson` list the same sites with the same fields |

## 5. Configuration and environment variables

**Pipeline files:**

| File | Purpose |
|---|---|
| `backend/config/targets.yaml`, `targets.d/*.yaml` | Moon and Mars targets (2.5) |
| `backend/config/sources.yaml` | Inbox folder and download options used by `analog sync` |
| `backend/config/mineral_groups.yaml` | EMIT mineral names → groups, detection threshold |
| `backend/config/crism_groups.yaml` | CRISM parameters → mineral-group thresholds |
| `data/labels/known_analogs.geojson`, `data/known_analogs.csv` | Known analog sites (training, validation, 50 km exclusion); the two files must list the same sites |

**Pipeline variables:**

| Variable | Default | Effect |
|---|---|---|
| `ANALOG_ROOT` | repository root | Use another project folder (must contain `data/` and `config/`; the tests use a temporary one) |
| `EARTHDATA_USERNAME`, `EARTHDATA_PASSWORD` | unset | Earthdata login, if you don't use the saved one |
| `METEOMATICS_USERNAME`, `METEOMATICS_PASSWORD` | unset | Meteomatics login (otherwise read from `meteomatics/credentials.md`) |
| `ANALOG_KEEP_PROJ_ENV` | unset | `1` keeps your own `PROJ_LIB` / `GDAL_DATA` |
| `ANALOG_LOG_DIR` | `data/logs` | Where command logs are written (2.10) |
| `ANALOG_NO_LOG_FILE` | unset | `1` disables the log file (progress is still shown) |

**Backend variables** (all optional locally; see [`backend/.env.example`](.env.example)):

| Variable | Local default | In production |
|---|---|---|
| `DJANGO_DEBUG` | `1` | `0` (the container's default) |
| `DJANGO_SECRET_KEY` | a dev key | **required**, from Secret Manager |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | e.g. `.run.app,.web.app` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `http://localhost:3000,...` | the site's `https://...` address (for the admin) |
| `DATABASE_URL` | SQLite | Cloud SQL Postgres URL |
| `TEAM_TOKEN` | `dev-token` | a shared secret; empty disables adding notes |
| `ANALOG_EXPORT_DIR` | `data/export` | `/app/data/export` in the container |

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `CRSError: ... proj.db ... comes from another PROJ installation` | Already handled: the package uses rasterio's bundled PROJ data. Set `ANALOG_KEEP_PROJ_ENV=1` to opt out |
| `Earthdata login failed` | Run `python -c "import earthaccess; earthaccess.login(persist=True)"` |
| `No EMIT L2B mineral granules here` | EMIT only maps arid regions; rely on terrain + HLS there |
| No terrain above 60°N or below 56°S | Outside SRTM: ingest a Copernicus DEM, REMA or ArcticDEM GeoTIFF with `--kind dem` |
| `No HLS L30 scenes found` | Widen `--start/--end` or raise `--max-cloud` (for `sync`: in `sources.yaml`) |
| `import`: "Nothing recognised" | Keep the original NASA file names; for other rasters pass `--kind` |
| `import`: "No region overlaps these files" | Run the `analog region create ...` command it prints, then import again |
| `skip HLS...: missing bands` | Download blue, red, NIR, SWIR1, SWIR2 and Fmask for each scene |
| `Grid would have N cells` | Use a smaller bbox or a larger `--cell` |
| A target has no candidates | Its regions are within 50 km of known analogs, or no cell meets a criterion: add a discovery region |
| Adding a note says "missing or wrong X-Team-Token" | Use the team token (`dev-token` locally) |
| `CERTIFICATE_VERIFY_FAILED` on spaceappschallenge.org | That site sends an incomplete certificate chain; read it in a browser. Never disable certificate checks |

## 7. Deployment

The API runs on Google Cloud Run behind Firebase Hosting (same address as the web app): build the image with `backend/cloudbuild.yaml`, deploy it to Cloud Run, and point the Firebase Hosting rewrite (`frontend/firebase.json`) at the service. Without a billing account, test the exact container locally:

```bash
# from the repository root
docker build -f backend/Dockerfile -t analog-api .
docker run --rm -p 8080:8080 -e DJANGO_SECRET_KEY=local-test -e DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1 -e TEAM_TOKEN=test analog-api
```

New results reach the deployed API only by rebuilding the image (the exported data is baked in).

## 8. Future work

### 8.1 An always-on API for the app

Today the web app runs as a static site and the API is optional. Next step: deploy the API permanently (Cloud Run is already set up in section 7) so the app can rely on it.

- **Serve every result on request:** regions, cells and candidates queried by bounding box, target and criterion, instead of whole JSON files baked into the site. The app then loads only what is on screen.
- **Store field reviews:** move the browser-only reviews into the database (a `Review` table next to `CandidateNote`). Add endpoints to post a review, list the reviews of a cell, and return a confidence summary per site.
- **Per-criterion ranking on the server:** "where to test slope" over all regions and world tiles, not only the region open in the browser.
- **Accounts or invitations** instead of one shared team token, so outside visitors can review a site without spam.
- **A persistent database:** PostgreSQL (Cloud SQL) instead of SQLite in the image, so reviews and notes survive redeploys. Read the exported data from a bucket instead of baking it into the image.

### 8.2 More and better data

- **Worldwide coverage at higher resolution:** run the Earth Engine screen over every continent, then refine the best tiles at ~1 km with the full pipeline.
- **Fill the gaps:** mineral layers (EMIT, ASTER) where coverage is thin (Mistastin, Kau Desert, Lanzarote). Add measured Moon/Mars target values (terrain from LOLA, HRSC and MOLA DEMs, CRISM minerals for every Mars target, thermal values from Diviner and TES) instead of estimates.
- **Neighbourhood features** (3–10 km terrain context) so craters and other large landforms are recognised.
- **Data for criteria 6 and 7** (biology, subsurface), which have no measure yet.

### 8.3 A better model

- **Learn from field reviews:** use cells that reviewers confirmed or rejected as extra positive and negative labels. Down-weight features that reviewers often find wrong. That closes the loop between satellite measures and ground truth.
- **Stronger classifiers:** gradient boosting and spatial cross-validation (leave one region out), with calibrated probabilities. Add uncertainty per cell so the app can show how sure each score is.
- **Learned target weights:** fit each target's feature weights on the known analogs instead of setting them by hand.
- **Automatic retraining and export** when new data or reviews arrive (a scheduled job), with the validation metrics tracked over time.
