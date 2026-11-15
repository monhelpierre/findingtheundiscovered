# Earth Analog Explorer

**Find places on Earth that resemble future Moon and Mars base sites, using open NASA data.**

Built for the **NASA Space Apps Challenge 2026**, challenge *"Identify Earth Locations that Analog the Permanent Moon Base Locations and Mars"*.

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
  - [4. Method and formulas](#4-method-and-formulas)
    - [4.1 Features](#41-features)
    - [4.2 Model A: similarity to a target](#42-model-a-similarity-to-a-target)
    - [4.3 Criteria and tiers](#43-criteria-and-tiers)
    - [4.4 Candidates](#44-candidates)
    - [4.5 Model B: positive-unlabeled classifier](#45-model-b-positive-unlabeled-classifier)
  - [5. Metrics and current results](#5-metrics-and-current-results)
    - [5.1 Validation: does the method find sites it has never seen?](#51-validation-does-the-method-find-sites-it-has-never-seen)
    - [5.2 Candidates (similarity index)](#52-candidates-similarity-index)
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
| No candidate within 50 km of a known analog | Enforced at export (23 known analogs) |
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

| Dataset | Gives | Used for |
|---|---|---|
| **SRTM** 30 m elevation (NASA) | slope, steep slope, share of gentle ground, roughness, relief | Terrain (2), operations (8) |
| **HLS** Harmonized Landsat-Sentinel 30 m (NASA) | albedo, NDVI, water, clay and ferric band ratios | Geology (1), bare-ground gate |
| **EMIT** L2B minerals (NASA, ISS) | clays, sulfates, carbonates, iron oxides, mafic minerals | Geology (1) |
| **CRISM** summary products (NASA, Mars) | the same mineral groups on Mars | Mars targets |
| **LOLA / MOLA / HRSC** DEMs via Moon & Mars Trek | terrain profiles of targets (`analog target from-dem`) | Moon/Mars targets |
| **Meteomatics** Weather API (Space Apps offer) | lowest temperature, daily and yearly range, soil moisture, frozen ground, dust, wind, aridity | Thermal (3), water & ice (4), surface processes (5) |
| **Google Earth Engine** (NASADEM, MODIS, JRC) | the same features for any place on Earth, without downloads | Discovery regions, whole-Earth screening |

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

1. **Exclude** every cell within **50 km** of any of the 23 known analogs (distance to the site's box).
2. **Qualify:** overall score ≥ 0.2 and at least one criterion met.
3. **Rank** by tier (A first), then score; keep cells **≥ 15 km apart**; keep the best 25 across regions.

### 4.5 Model B: positive-unlabeled classifier

- **Positives:** cells inside known analog sites, one model per analog type (basaltic volcanic, impact crater, ...). **Unlabeled:** all other cells. They are not negatives (some are undiscovered analogs).
- **Bagging:** 10 random forests (200 trees each), each trained against a fresh random background sample of max(3 × positives, 500) cells; probabilities are averaged.
- **Features:** those present in ≥ 50% of the type's cells, minus elevation and water (they encode *where* a site is, not *what* it is). Climate features are left out too (`--skip-meteomatics`; they made validation fail, section 5.1).

## 5. Metrics and current results

8 study regions, 19,725 cells; 23 known analogs (17,027 cells lie within 50 km of one and are never candidates).

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
- **Known analogs**: 11 of the 23 have *proposed* criteria to review (`criteria_source` in [data/known_analogs.csv](data/known_analogs.csv)).


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

Team (NASA Space Apps Challenge 2026, Sherbrooke local event): Guilhem, Eli, Meriem, Monhel, Leea.

**License:** MIT ([LICENSE](LICENSE)). Data credits: NASA LP DAAC (SRTM, HLS, EMIT), NASA JPL / PDS (CRISM, LOLA, MOLA), ESA, Meteomatics, and the Google Earth Engine catalog datasets (NASADEM, MODIS, JRC Global Surface Water).
