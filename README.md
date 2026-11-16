# Earth Analog Explorer

**Find places on Earth that resemble future Moon and Mars base sites, using open NASA data.**

Built for the **NASA Space Apps Challenge 2026**, challenge *"Identify Earth Locations that Analog the Permanent Moon Base Locations and Mars"*.

**Live app: <https://frontend-henna-mu-96.vip.eu.com>**

| Part | What it is | Read |
|---|---|---|
| **Backend** | The `analog` pipeline (data, features, scoring, classifier, export) and an optional Django API | [backend/README.md](backend/README.md) |
| **Frontend** | The web app: map in five colour-blind-safe score classes, targets by mission (habitat / landing), one criterion at a time, candidates, criteria radar, field reviews, validation | [frontend/README.md](frontend/README.md) |

## Contents

- [Earth Analog Explorer](#earth-analog-explorer)
  - [Contents](#contents)
  - [1. Goal](#1-goal)
  - [2. How it works](#2-how-it-works)
  - [3. Data](#3-data)
    - [3.1 Earth: study regions](#31-earth-study-regions-1-km-cells-downloaded)
    - [3.2 Earth: worldwide screen](#32-earth-worldwide-screen-5-km-cells-google-earth-engine)
    - [3.3 Moon and Mars targets](#33-moon-and-mars-targets)
    - [3.4 Known analogs, Earth reference and the sister project](#34-known-analogs-earth-reference-and-the-sister-project)
  - [4. Method and formulas](#4-method-and-formulas)
    - [4.1 Features](#41-features)
    - [4.2 Model A: similarity to a target](#42-model-a-similarity-to-a-target)
    - [4.3 Criteria and tiers](#43-criteria-and-tiers)
    - [4.4 Candidates](#44-candidates)
    - [4.5 Model B: positive-unlabeled classifier](#45-model-b-positive-unlabeled-classifier)
  - [5. Metrics and current results](#5-metrics-and-current-results)
    - [5.1 Validation: does the method find sites it has never seen?](#51-validation-does-the-method-find-sites-it-has-never-seen)
    - [5.2 Candidates (similarity index)](#52-candidates-similarity-index)
    - [5.3 Weight sensitivity](#53-weight-sensitivity)
    - [5.4 Sister project: the Moon-base profile](#54-sister-project-the-moon-base-profile)
  - [6. Quick start](#6-quick-start)
  - [7. Repository](#7-repository)
  - [8. Limitations and next steps](#8-limitations-and-next-steps)
  - [9. Use of AI](#9-use-of-ai)
  - [10. Team and license](#10-team-and-license)

---

## 1. Goal

Astronauts, rovers and instruments are tested on Earth in **analog sites**: places that resemble the Moon or Mars in some useful way (Hawaiian lava for lunar regolith, the Atacama for Martian dryness, Arctic craters for impact geology). The well-known ones are few and heavily used.

**Our goal:** build an open, reproducible ranking that compares Earth's land with NASA's candidate sites on the Moon and Mars, on the project brief's **8 analog criteria**, and finds **new** candidate analogs, not the ones already known. For every candidate it must show *where the analogy holds and where it breaks down*.

Success criteria (from the team brief):

| Criterion | How we measure it |
|---|---|
| At least 3 new candidate sites | Candidates per target after excluding known analogs (section 5.2) |
| No candidate within 50 km of a known analog | Enforced at export (33 known analogs) |
| Known analogs, hidden from the model, rank in the top 10% | Leave-one-site-out validation (section 5.1) |
| Every candidate explained | Per-criterion scores, radar chart and per-feature match bars in the app |
| Code and method public; demo runs in a browser | This repository (MIT); the static web app |

## 2. How it works

```text
 Moon / Mars target sites                    Earth study regions (~1 km cells)
 (Artemis III regions, rover sites)          SRTM terrain, HLS surface, EMIT minerals,
 profile: value ± tolerance per feature      Meteomatics climate, Earth Engine (any place)
                │                                          │
                └────────────► compare, feature by feature ◄┘
                                        │
             Model A: similarity index (formula)  +  Model B: classifier (learned from known analogs)
                                        │
             8 criteria → tiers A–D → candidates (≥ 50 km from known analogs, ≥ 15 km apart)
                                        │
                         JSON export → web map, criteria radar, validation
```

1. **Describe each Moon/Mars target in numbers** with the same features Earth cells have (slope, roughness, minerals, temperature...), each with a tolerance.
2. **Measure every ~1 km Earth cell** in the study regions from satellite data.
3. **Score each cell against each target** (Model A), group the scores into the 8 criteria, and give each cell a tier.
4. **Learn from known analogs** (Model B): a classifier trained on known sites, tested on sites it has never seen.
5. **Pick candidates** far from known analogs and **explain** each one.

## 3. Data

Every source: where it comes from, exactly what the pipeline asks for, and why we chose it. The parameters are the defaults in the code (`backend/src/analog/fetch.py`, `meteomatics.py`, `backend/gee/analog_features.js`); the options in [backend/README.md](backend/README.md#29-command-reference) change them.

### 3.1 Earth: study regions (~1 km cells, downloaded)

| Dataset | Link | What we request (parameters) | Gives | Why this one |
|---|---|---|---|---|
| **SRTM GL1** v003, 30 m elevation (NASA / USGS LP DAAC) | <https://lpdaac.usgs.gov/products/srtmgl1v003/> | `earthaccess` search: `short_name="SRTMGL1"`, `version="003"`, the region's bounding box. Every tile is used | mean slope, 75th-percentile slope, roughness, relief, share of land at 0–15°, elevation (display only) | The free, near-global 30 m elevation model (60° N–56° S): fine enough to measure slope and roughness inside a 1 km cell, and close to the resolution of the Moon and Mars models we compare with |
| **HLS L30** v2.0, Harmonized Landsat-Sentinel 30 m (NASA LP DAAC) | <https://lpdaac.usgs.gov/products/hlsl30v002/> | `short_name="HLSL30"`, `version="2.0"`, dates `2024-03-01` to `2025-10-31`, cloud cover ≤ 10%, up to 500 scenes; per MGRS tile, the 2 scenes with the most coverage and fewest clouds. Bands B02, B04, B05, B06, B07 and the Fmask cloud/water mask | albedo (Liang 2001), NDVI, water fraction, clay index (SWIR1/SWIR2), ferric index (red/blue) | 30 m surface reflectance, atmospherically corrected and identical for Landsat and Sentinel-2. Albedo and the band ratios tell what the ground is made of; NDVI checks that it is bare, like the Moon and Mars. Low cloud cover and two scenes per tile fill the gaps of a single pass |
| **EMIT L2B** mineral identification (NASA LP DAAC, on the ISS) | <https://lpdaac.usgs.gov/products/emitl2bminv001/> | `short_name="EMITL2BMIN"`, the region's bounding box, all dates (or `--start`/`--end`), up to 5 granules, the newest collection version of each acquisition. A pixel counts as a detection when its band depth is ≥ 0.08 (`backend/config/mineral_groups.yaml`) | share of clays, sulfates, carbonates, iron oxides and mafic minerals per cell | The only free imaging spectrometer that names minerals from orbit (60 m), in the same mineral groups CRISM maps on Mars. It covers arid land only (~52° N–52° S), which is where most analogs are |
| **Meteomatics Weather API** (Space Apps 2026 data offer) | <https://api.meteomatics.com>; parameter list: <https://www.meteomatics.com/en/api/available-parameters/> | Daily values for one year (`--year 2024`) on a 0.05° grid over the region, in 12 monthly requests. Parameters: `t_min_2m_24h:C`, `t_max_2m_24h:C`, `wind_speed_mean_10m_24h:ms`, `dust_aod_550nm:idx`, `soil_moisture_index_-5cm:idx`, `precip_24h:mm`, `evapotranspiration_24h:mm`, `snow_depth:cm`, `frost_depth:cm` | lowest temperature, daily and yearly temperature range, soil moisture, frozen ground (≥ 15 days a year), dust, wind, aridity | One consistent source for every climate quantity the criteria need (thermal 3, water and ice 4, dust and wind 5), anywhere on Earth, from one API, free for the challenge. A full year of daily values captures the extremes |
| **Noon sun elevation, low-sun days** | computed, no download | from each cell's latitude: the mean noon elevation over the year, and the days when the noon Sun stays under 10° | criterion 3 (illumination) | Pure geometry, so every cell has it with no data. Low-sun days is the Artemis III illumination measure of the sister project |

### 3.2 Earth: worldwide screen (~5 km cells, Google Earth Engine)

Script `backend/gee/analog_features.js`: one export per continent, cells of 0.05°, MODIS medians over `2024-01-01` to `2025-01-01` (daily products sampled on days 1, 9, 17 and 25 of each month).

| Dataset (Earth Engine ID) | Link | Gives | Why this one |
|---|---|---|---|
| **NASADEM** 30 m (`NASA/NASADEM_HGT/001`) | <https://developers.google.com/earth-engine/datasets/catalog/NASA_NASADEM_HGT_001> | the same terrain statistics as SRTM, plus passable ground within 2, 10 and 50 km | NASA's reprocessed SRTM: the same definitions as in the study regions, so the scores compare |
| **MODIS MCD43A3** albedo (`MODIS/061/MCD43A3`) | <https://developers.google.com/earth-engine/datasets/catalog/MODIS_061_MCD43A3> | white-sky shortwave albedo | Global and daily with no download; 500 m instead of HLS's 30 m |
| **MODIS MOD13A1** vegetation (`MODIS/061/MOD13A1`) | <https://developers.google.com/earth-engine/datasets/catalog/MODIS_061_MOD13A1> | NDVI (bare-ground check) | Same reason |
| **MODIS MOD11A2** land surface temperature (`MODIS/061/MOD11A2`) | <https://developers.google.com/earth-engine/datasets/catalog/MODIS_061_MOD11A2> | day − night surface temperature range | The thermal criterion's Earth-wide measure where there is no Meteomatics data |
| **MODIS MCD43A4** reflectance (`MODIS/061/MCD43A4`) | <https://developers.google.com/earth-engine/datasets/catalog/MODIS_061_MCD43A4> | clay and ferric indices (bands 6/7 and 1/3) | The same band ratios as with HLS, everywhere |
| **JRC Global Surface Water** v1.4 (`JRC/GSW1_4/GlobalSurfaceWater`) | <https://developers.google.com/earth-engine/datasets/catalog/JRC_GSW1_4_GlobalSurfaceWater> | water fraction (water ≥ 50% of the time); also masks water out of the passable ground | Decades of Landsat water history at 30 m, so lakes and coasts don't pass as flat bare ground |
| **ERA5-Land** monthly (`ECMWF/ERA5_LAND/MONTHLY_AGGR`), 2015–2024 | <https://developers.google.com/earth-engine/datasets/catalog/ECMWF_ERA5_LAND_MONTHLY_AGGR> | permafrost depth class: the shallowest soil layer (0–7, 7–28, 28–100 cm) whose warmest month stays below 0 °C | Ten years of soil temperature, consistent across continents; the sister project's measure of ice for the Moon base |

### 3.3 Moon and Mars targets

| Dataset | Link | What we use | Why |
|---|---|---|---|
| **LOLA** (Moon), **MOLA** and **HRSC** (Mars) elevation models, through NASA Moon Trek and Mars Trek | <https://trek.nasa.gov/moon/>, <https://trek.nasa.gov/mars/> | GeoTIFF clips around a site, turned into the same terrain statistics as on Earth (`analog target from-dem`) | The reference elevation models of each body; Trek clips any site without mission-specific tools |
| **CRISM** MTRDR summary products (Mars Reconnaissance Orbiter), PDS Geosciences Node | <https://ode.rsl.wustl.edu/mars/> | spectral parameters (e.g. OLINDEX3, D2300) turned into the same mineral groups as EMIT (`analog import FOLDER --target mars_jezero`) | Measured minerals for a Mars target instead of estimates (Jezero so far) |
| **Values from the literature** (LRO Diviner temperatures, LEND and Lunar Prospector hydrogen, mission reports) | sources noted next to each value in [`backend/config/targets.yaml`](backend/config/targets.yaml) | estimated values and percentiles, marked as estimates | Where no measured map has been clipped yet; to be replaced by measurements |

### 3.4 Known analogs, Earth reference and the sister project

| Dataset | Link | What we use | Why |
|---|---|---|---|
| **Known analog sites** | [`data/known_analogs.csv`](data/known_analogs.csv), outlines in [`data/labels/known_analogs.geojson`](data/labels/known_analogs.geojson) | 33 sites: 23 from the team, 10 training sites from the sister project (approximate positions) | They train and test the classifier, and 50 km around them is excluded so every result is new |
| **Earth reference percentiles** | Meteomatics, 5° global grid, land between 56° S and 72° N, days 1, 9, 17 and 25 of each month of 2024 (`analog earth-ref build`) | where a cell's climate ranks among Earth's land | Earth can't reach the Moon's extremes, so some features compare ranks instead of values |
| **Moon-base sites**, *Finding the Undiscovered* (MIT) | <https://github.com/The-Djomekou/finding-the-undiscovered> (`data/processed/sites.parquet`) | 18,516 sites scored against Artemis III, shown as a layer in the app (`analog moonbase`) | A whole-Earth answer to the Moon-base question, next to our target-by-target one |

**Accounts:** SRTM, HLS and EMIT need a free NASA Earthdata login (<https://urs.earthdata.nasa.gov>); Meteomatics needs the challenge credentials; Earth Engine needs a free non-commercial account (<https://code.earthengine.google.com/register>).

How to add each dataset: [backend/README.md](backend/README.md#24-adding-data).

## 4. Method and formulas

### 4.1 Features

Every cell (0.01°, about 1 km) gets up to ~30 features. The scored ones:

| Criterion | Features |
|---|---|
| 1 Geology | albedo, clay and ferric indices, EMIT mineral shares (`min_*`) |
| 2 Terrain | mean slope, 75th-percentile slope, roughness (std of elevation), relief (max − min) |
| 3 Thermal | day–night surface temperature range; lowest temperature, daily and yearly air-temperature range, noon sun elevation |
| 4 Water & ice | water fraction, soil moisture, frozen ground |
| 5 Surface processes (Mars only) | dust optical depth, wind speed |
| 6 Biology, 7 Subsurface | not scored: left to the site dossiers |
| 8 Operations | **share of the cell's land at 0–15° slope** (water and ocean excluded) |

**NDVI is a gate**, not a criterion: Moon and Mars have no vegetation, so a cell must be bare (NDVI similarity ≥ 0.70, i.e. NDVI ≲ 0.15) to get a tier.

### 4.2 Model A: similarity to a target

For a cell value $x$ and a target feature with value $p$, tolerance $\sigma$ and weight $w$:

$$
z = \frac{x - p}{\sigma}\ \ (\text{near}), \qquad
z = \max\!\Big(0, \frac{p - x}{\sigma}\Big)\ \ (\text{at least}), \qquad
z = \max\!\Big(0, \frac{x - p}{\sigma}\Big)\ \ (\text{at most})
$$

$$
s = e^{-z^2/2} \qquad\qquad
\text{score} = \exp\!\Big(-\tfrac12\,\frac{\sum_i w_i z_i^2}{\sum_i w_i}\Big) = \Big(\prod_i s_i^{\,w_i}\Big)^{1/\sum w_i}
$$

- $s$ is the per-feature similarity (1 = identical; 1σ off → 0.61; 2σ → 0.14).
- The score is the **weighted geometric mean** of the $s_i$: one badly mismatched feature pulls it down and can't be bought back by good ones.
- Features a cell lacks are skipped; a cell needs data for at least half of the target's weight (**coverage ≥ 0.5**), and cells that are more than half water get no score.
- One-sided modes handle Earth's limits (e.g. a Moon-like 300 K temperature swing is impossible on Earth: "at least 25 K" counts as a match).

**Percentile features (relative comparison).** Earth can't reach the Moon's −230 °C or its polar hydrogen, so for some features the target gives *where it ranks on its own world* instead of a value, and a cell is compared by *where it ranks on Earth's land*:

$$
x = \Phi^{-1}\big(\text{rank of the cell's value among Earth's land}\big), \qquad
p = \Phi^{-1}\big(\text{target's percentile on its own world}\big), \qquad
z = \frac{x - p}{\sigma},\ \sigma = 1 \text{ by default}
$$

$\Phi^{-1}$ is the inverse normal CDF; ranks are clipped to 0.001–0.999. Earth's ranks come from area-weighted quantiles over Earth's land between 56° S and 72° N (`config/earth_reference.json`, built from a global 5° Meteomatics grid by `analog earth-ref build`). Example: Malapert's noon sun is lower than on 99.7% of the Moon (percentile 0.003, from geometry: $1 - \sin|\text{lat}|$), so the best thermal matches on Earth are the land cells with the lowest sun. Current percentiles: noon sun elevation for all targets (geometry), and for the Moon targets lowest temperature (0.02), daily temperature range (0.05) and hydrogen (0.95): estimates from LRO Diviner and LEND, to refine.

### 4.3 Criteria and tiers

Each criterion's score uses the same formula, restricted to that criterion's features:

| Criterion score | Band | Counts as met? | Meaning in σ |
|---|---|---|---|
| ≥ 0.85 | strong match | yes | every feature within ~0.57σ |
| 0.70 – 0.84 | match | yes | within ~0.85σ |
| 0.50 – 0.69 | near miss | no (reported) | within ~1.18σ |
| < 0.50 | mismatch | no | |

$$
\text{match ratio} = \frac{\text{criteria met}}{\text{criteria measured in the cell}}
\qquad
\text{tier} = \begin{cases} A & \ge 0.75 \\ B & \ge 0.50 \\ C & \ge 0.25 \\ D & > 0 \end{cases}
$$

### 4.4 Candidates

For each target, in each region:

1. **Exclude** every cell within **50 km** of any of the 33 known analogs (distance to the site's box).
2. **Qualify:** overall score ≥ 0.2 and at least one criterion met.
3. **Rank** by tier (A first), then score; keep cells **≥ 15 km apart**; keep the best 25 across regions.

### 4.5 Model B: positive-unlabeled classifier

- **Positives:** cells inside known analog sites, one model per analog type (basaltic volcanic, impact crater, ...). **Unlabeled:** all other cells. They are not negatives (some are undiscovered analogs).
- **Bagging:** 10 random forests (200 trees each), each trained against a fresh random background sample of max(3 × positives, 500) cells; probabilities are averaged.
- **Features:** those present in ≥ 50% of the type's cells, minus elevation and water (they encode *where* a site is, not *what* it is). Climate features are left out too (`--skip-meteomatics`; they made validation fail, section 5.1).

## 5. Metrics and current results

8 study regions, 19,725 cells; 33 known analogs (17,027 cells lie within 50 km of one and are never candidates).

### 5.1 Validation: does the method find sites it has never seen?

**Leave one site out:** hide a known site, train on the others of its type, and check how the hidden site ranks.

| Metric | Definition |
|---|---|
| **AUC** | Probability that a random cell of the hidden site outranks a random background cell (0.5 = chance, 1 = perfect) |
| **AUC (bare)** | The same against bare ground only (NDVI < 0.2): the honest test, since lava vs. forest is trivial |
| **Beats background** | Share of background cells below the site's mean probability |
| **Top 10%** | Passes when the site beats ≥ 90% of background (the brief's success test) |

| Analog type | Hidden site | AUC | AUC (bare) | Beats background | Top 10% |
|---|---|---|---|---|---|
| Basaltic volcanic | Kaʻū Desert, Hawaiʻi | 0.888 | 0.861 | 97.0% | ✅ pass |
| Basaltic volcanic | Timanfaya, Lanzarote | 0.890 | 0.912 | 94.7% | ✅ pass |
| Impact crater | Meteor Crater, Arizona | 0.350 | 0.542 | 49.1% | ❌ fail |
| Impact crater | Mistastin Lake, Labrador | 0.308 | 0.208 | 50.4% | ❌ fail |

- **Volcanic analogs transfer:** a model that has only seen Hawaiian lava recognises Lanzarote's, and vice versa.
- **Craters don't:** a crater is a kilometre-scale *shape* (a round rim) that per-cell features can't see; neighbourhood features are the planned fix.
- **Climate hurts the classifier:** with the Meteomatics features, both volcanic sites fail (AUC 0.69 and 0.78), because climate encodes location. They are used for similarity, not for learning.
- The other types have one site inside a region each and can't be tested yet.

### 5.2 Candidates (similarity index)

After the 50 km exclusion, per target:

| Target | Body | Candidates | Tiers | Best candidate (region, lat, lon, score, criteria met) |
|---|---|---|---|---|
| Malapert Massif | Moon | 3 | B 2, C 1 | Atacama, −24.535, −69.465, 0.40 (B, 3/5) |
| Mons Mouton Plateau | Moon | 4 | A 1, B 3 | Haiti NW, 19.605, −72.985, 0.49 (A, 3/4) |
| Jezero Crater | Mars | 5 | A 5 | Haiti NW, 19.555, −72.905, 0.87 (A, 4/4) |
| Gale Crater (Mount Sharp) | Mars | 5 | A 3, B 2 | Atacama, −24.425, −69.415, 0.91 (A, 3/4) |
| Arcadia Planitia | Mars | 4 | A 3, B 1 | Haiti NW, 19.565, −72.925, 0.94 (A, 3/4) |
| Meridiani Planum | Mars | 4 | A 1, B 2, C 1 | Haiti NW, 19.565, −72.925, 0.92 (A, 3/4) |

**What the percentile features changed:** criterion 3 (thermal) is now scored for every target, and criterion 4 (hydrogen) for the Moon. Malapert's best cells dropped from A (3/3) to B (3/5), and Mons Mouton's only A is a Haiti cell where hydrogen isn't measured (3/4): both sites are among the coldest, lowest-sun, most hydrogen-rich places on the Moon, and no study region so far is cold or polar (thermal and hydrogen criteria ≈ 0.01–0.03). The terrain, geology and operations matches still hold. Jezero now meets 4 of 4 criteria in Haiti's north-west. Next: a cold, high-latitude discovery region for the Moon targets.

**Worldwide screen:** `analog world import` cuts the Earth Engine continent exports into 5° tiles automatically, with no regions to draw. Europe alone gives 56 tiles and 560,000 cells of ~5 km, and 25 candidates per target outside the known-analog zones (the app's **Worldwide** checkbox). These tiles have terrain, surface and sun data but no Meteomatics climate yet, so their thermal criterion uses sun elevation only.

Every target has **at least 3 new candidates** in the study regions, all outside the known-analog zones. Only two regions produce candidates (the Atacama's southern edge and Haiti's north-west), because the other study regions were drawn around known analogs; **discovery regions** (Earth Engine or new downloads) are the way to more results.

### 5.3 Weight sensitivity

`analog sensitivity` multiplies every feature weight of one criterion by 0.8, then 1.2, re-scores the study regions, and counts how many of the target's 100 best eligible cells stay in the top 100 (the check the sister project uses for its weights):

| Target | Eligible top cells | Kept (worst case) |
|---|---|---|
| Malapert Massif | 42 | 31–42 (criterion 3 × 1.2) |
| Mons Mouton Plateau | 100 | 99–100 |
| Jezero Crater | 100 | 97–100 |
| Gale Crater | 100 | 98–100 |
| Arcadia Planitia | 100 | 98–100 |
| Meridiani Planum | 100 | 99–100 |

The Mars rankings are stable. Malapert's is not yet: only 42 cells qualify, all close to the thresholds, so a 20% change in the thermal or geology weights reorders up to a quarter of them. That is one more sign that the study regions hold no good Malapert analog (section 5.2).

### 5.4 Sister project: the Moon-base profile

*Finding the Undiscovered*, a second Sherbrooke project, scores all of Earth's land at 10 km against the Artemis III Moon-base requirements with fixed weights (illumination first). It finds 18,516 sites; the best are near the Arctic Circle (West Greenland, Baffin Island). The app shows them as a layer (**Moon-base sites, whole Earth**), next to our target-by-target results. We also took its 10 extra known analogs, its low-sun days, permafrost and passable-ground features, and its weight-sensitivity check ([backend/README.md](backend/README.md#211-sister-project-the-moon-base-profile)).

These numbers change with every `analog train` / `analog export`; the app always shows the current ones.

## 6. Quick start

```bash
# Web app only (the current results are already in frontend/public/data)
cd frontend && npm install && npm run dev            # http://localhost:3000

# Pipeline (Python 3.10+)
python -m venv .venv && .venv\Scripts\activate       # macOS/Linux: source .venv/bin/activate
pip install -e "backend[dev]"
analog region create my-site --bbox W S E N
analog sync my-site --run                            # download, features, scores, export
```

Everything else (importing data, Meteomatics, Earth Engine, training, the API): [backend/README.md](backend/README.md). The app in detail: [frontend/README.md](frontend/README.md).

## 7. Repository

```text
.
├── backend/      # Python: `analog` pipeline (src/analog, config/), Django API (server/, api/), tests/, Earth Engine script (gee/)
├── frontend/     # Next.js web app (app/, components/, lib/), its data (public/data/), tests/
├── data/         # known analogs (labels/, known_analogs.csv), feature schema, regions, exports, logs (large parts git-ignored)
├── .github/      # CI: backend lint + tests, frontend typecheck + tests on every push
├── README.md
└── LICENSE       # MIT
```

## 8. Limitations and next steps

- **Target values are mostly estimates**: only Jezero's minerals are measured (CRISM). Measure terrain with `analog target from-dem` from LOLA/HRSC clips.
- **Criteria 3–5 are partly scored**: criterion 3 (thermal) and, for the Moon, 4 (hydrogen) use percentile values (section 4.2), mostly estimates; Mars water, dust and wind percentiles are still to set (from Mars Odyssey GRS, MCD/TES).
- **Few candidate regions**: add discovery regions away from known analogs; the Earth Engine script can screen whole continents at ~5 km.
- **Craters fail validation**: add neighbourhood (3–10 km) terrain features.
- **Known analogs**: 21 of the 33 have *proposed* criteria and types to review (the 10 added from the sister project's training sites have approximate positions) (`criteria_source` in [data/known_analogs.csv](data/known_analogs.csv)).
- **Static app**: the web app reads files built into the site, and field reviews stay in each browser until they are added to `frontend/public/reviews.json`.

Future work in detail:
- **Backend** ([backend/README.md](backend/README.md#8-future-work)): an always-on API that serves the data on request and stores field reviews, worldwide data at higher resolution, and a better model that learns from the reviews.
- **Frontend** ([frontend/README.md](frontend/README.md#7-future-work)): a dynamic app backed by that API, field tools (photos, observed values, offline mode, GPX export), and uncertainty on the map.


## 9. Use of AI

Space Apps asks teams to be open about AI use. We used an AI coding assistant (Claude Code, by Anthropic) as a pair programmer:

| What the AI helped with | What stayed with the team |
|---|---|
| **Moving fast from idea to working prototype:** first versions of the pipeline (download, features, scoring), the web app and the Earth Engine script, from the team's brief | The challenge reading, the idea, the 8 criteria and their meaning, the Moon/Mars targets, the known analogs and which criteria each is known for, the 0–15° slope rule, the choice of data sources |
| **Tests:** writing and extending the automated tests (backend pytest, frontend Vitest), including the checks that the browser and Python compute the same scores | Deciding what must be true (identical cell scores 1, the 50 km exclusion, top-10% validation) |
| **Restructuring the project professionally:** the `backend/` / `frontend/` split, configuration files, logging, deployment files, and the documentation (these READMEs) | Reviewing the code, running the pipeline on real data, checking the results and maps, and every final decision |
| **Debugging:** e.g. Earth Engine pixel limits, library version conflicts | |

No AI-generated images, data or results: every number in this README comes from running the pipeline on the cited datasets, and can be reproduced with the commands in [backend/README.md](backend/README.md). AI output was reviewed by the team and checked by the tests before being kept.

## 10. Team and license

Team (NASA Space Apps Challenge 2026, Sherbrooke local event):

- **Eli:** conceptualization, methodology, investigation, writing (original draft)
- **Meriem:** methodology, investigation, final written package and proofreading
- **Guilhem:** methodology, final written package and proofreading
- **Monhel:** machine learning, frontend design, project deployment
- **Leea:** final written package and proofreading

**License:** MIT ([LICENSE](LICENSE)). Data credits: NASA LP DAAC (SRTM, HLS, EMIT), NASA JPL / PDS (CRISM, LOLA, MOLA), ESA, Meteomatics, and the Google Earth Engine catalog datasets (NASADEM, MODIS, JRC Global Surface Water, ERA5-Land). Moon-base sites: *Finding the Undiscovered* (MIT).
