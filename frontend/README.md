# Frontend: the web app

The map of [Earth Analog Explorer](../README.md): pick a Moon or Mars target, see how well every ~1 km cell of each study region matches it, and inspect the best candidate sites, criterion by criterion. Next.js 15 (App Router), React 19, MapLibre GL 5, TypeScript.

It needs only the JSON written by the pipeline (`analog export`, see [backend/README.md](../backend/README.md)), which is copied into `public/data/`. It runs as a **static site** (Vercel, Firebase Hosting, any static host). When the optional [Django API](../backend/README.md#3-django-api) is running, it also offers weight sliders and team notes.

## Contents

1. [Setup](#1-setup)
2. [Running and using the app](#2-running-and-using-the-app)
3. [Tests](#3-tests)
4. [Configuration](#4-configuration)
5. [Deployment](#5-deployment)
6. [Troubleshooting](#6-troubleshooting)
7. [Future work](#7-future-work)

---

## 1. Setup

Node.js 18.18+ (tested with 22).

```bash
cd frontend
npm install
npm run dev            # http://localhost:3000
```

```text
frontend/
├── app/                      # layout, page, styles (light and dark theme)
├── components/               # Explorer (side panel), MapView (MapLibre), CriteriaRadar, ClientShell
├── lib/                      # types, the scoring maths (mirrors Python), API client with static fallback
├── public/data/              # copy of data/export/: what the site shows (committed)
├── scripts/sync-data.mjs     # copies ../data/export into public/data
├── tests/                    # Vitest
├── firebase.json, firebase.static.json, .firebaserc   # Firebase Hosting, with / without the API rewrite
└── next.config.ts            # dev: /api forwarding; build: static export to out/
```

## 2. Running and using the app

[`frontend/`](): Next.js 15 (App Router), React 19, MapLibre GL 5, TypeScript.

### 2.1 Run it

```bash
cd frontend
npm run dev            # http://localhost:3000
```

| Mode | What you get |
|---|---|
| Frontend only | Data from `public/data/` (the last export). Everything works except weight sliders and notes |
| Frontend + backend (start `runserver` **first**) | If a backend answers at `http://127.0.0.1:8000` when `npm run dev` starts (or `BACKEND_URL` is set), `/api/**` is forwarded to it. The footer says **"served by the backend"**. Started the backend later? Restart `npm run dev` |

### 2.2 Using the app

| Part | What it does |
|---|---|
| **Colour the map by** | Similarity to one Moon or Mars target (grouped by mission: habitat or landing), or (if trained) the classifier's "any analog" or one analog type |
| **What to test** | The full mission profile, or **one criterion alone** (geology, terrain, thermal…) compared with the chosen target. The map and the candidate list then show where to test that criterion. The candidates are computed in the browser for the open region (outside the known-analog zones, 15 km apart). Criteria without a measure, or without data in the region, are greyed out |
| **Legend** | Five score classes, same bounds as the project's thresholds: very low < 0.25, low 0.25–0.50, moderate 0.50–0.70, high 0.70–0.85, very high ≥ 0.85. Colours are five steps of viridis (colour-blind safe). Click a class to keep it and the ones above; the slider does the same in steps of 0.05 |
| **Target card** | The target's profile: value ± sigma (or ≥ / ≤) and weight per feature. With the backend, each weight is a **slider**: the map and candidates are re-scored for the current region; **Reset weights** goes back |
| **Classifier card** | Training summary and validation per hidden site, with the **top-10%** pass/fail |
| **Earth region** | Switches region and lists its layers |
| **No time? The idea in 30 seconds** | A card at the top with a 4-step picture of the method (pick a site, measure Earth, classify, shortlist) and what to try first. It can be closed; the browser remembers it |
| **Map** | Satellite imagery with the score classes, the region outline and numbered candidates (same numbers as the list). Hover for a class and score; click a cell for details. Changing site or criterion zooms to the new candidates. The **Google / Bing / Esri** buttons switch the imagery: Esri has no close-zoom data in many regions, Google and Bing go further (unofficial tile endpoints, fine for research use; use an official provider for a public site) |
| **Selected cell** | Coordinates and a Google Maps link, then three sections. **Overall score and tier**, with a note if the cell is near a known analog. **Criteria:** a **radar chart** and a table of each criterion's score and band. **Features:** a match bar per feature. Then **field reviews** (below). With the backend, also **team notes**. **Download CSV** saves all of it for that site (site and score, each criterion, each feature with the target value and match, field-review confidence) as a spreadsheet |
| **Top candidates** | Best cells across regions, ordered by tier then score, with their tier badge, class colour and field-review confidence. Click one to fly there. With one criterion selected: **Where to test**, the best cells of the open region for that criterion |
| **Footer** | When the data was exported, where it came from (backend or static copy), and **Reload data** |

The layout adapts to phones and follows the system's light or dark theme.

**Field reviews** (no backend needed). Someone who visited a site, or checked it on imagery, says per criterion whether the data shown **matches** what they saw, rates the overall **reliability** from 1 to 5, and can give a verdict and a comment. The reviews of a cell make a **confidence indicator** (high, medium, low, data contested; a site visit counts twice an imagery check), shown on the cell and in the candidate list. Criteria that reviewers say are wrong are flagged in the criteria table.

- A new review is saved **in that browser** only. **Download them** gives a JSON file.
- To share reviews with everyone, add the downloaded entries to `public/reviews.json` (a JSON array) and redeploy. It lives outside `public/data/`, so `analog export` doesn't overwrite it.

### 2.3 How it works

- **Loading:** the app asks the backend first (`/api/index`, `/api/regions/<id>`) and falls back to the static files (`/data/index.json`, `/data/regions/<id>.json`). The site therefore always shows the map, even with no backend.
- **Same maths in the browser:** `lib/similarity.ts` computes the per-feature similarity, the criteria and the tiers for the selected cell with the same formulas as Python. A test checks it against every exported candidate.
- **Production build:** `npm run build` writes a **static site** to `frontend/out/`, ready for Firebase Hosting or any static host. In development, `next.config.ts` adds the `/api` forwarding instead.
- **Data copy:** `public/data/` is a copy of `data/export/`. `analog export` refreshes it, and `npm run sync-data` does the same by hand; it also runs automatically before `dev` and `build`. When `data/export/` doesn't exist (e.g. on a build server), the committed copy is kept.

### 2.4 Scripts

```bash
npm run dev          # development server (forwards /api to the backend)
npm run build        # static site in out/
npx serve out        # preview the static build locally
npm test             # Vitest
npm run typecheck    # TypeScript
npm run sync-data    # copy ../data/export into public/data
```

### 2.5 Worldwide screen and panel width

- **Worldwide** (checkbox above the region menu, shown when the export has world tiles from `analog world import`): the candidate list becomes the best cells over every Earth Engine tile instead of the team's study regions, and the region menu lists the tiles that hold them. Known analogs and their 50 km zones stay excluded.
- **Panel width:** drag the bar between the panel and the map (300–900 px). The width is remembered in this browser.

## 3. Tests

```bash
npm test               # Vitest (90 tests, a few seconds)
npm run typecheck      # TypeScript
```

| Suite | Covers |
|---|---|
| [`tests/similarity.test.ts`](tests/similarity.test.ts) | Per-feature similarity, criteria grouping, NDVI gate, tiers and bands |
| [`tests/api.test.ts`](tests/api.test.ts) | Backend-first loading with static fallback (HTML 404, network error), weights as query parameters, team-token header, error messages |
| [`tests/export-consistency.test.ts`](tests/export-consistency.test.ts) | For every exported candidate, the browser's criteria scores and tier equal Python's |
| [`tests/criterion-review.test.ts`](tests/criterion-review.test.ts) | Score classes and their bounds; one criterion alone matches Python's per-criterion scores, and its best cells stay apart and outside the known-analog zones; field-review confidence (site visits weigh twice, contested data, shared + local reviews without duplicates); the site CSV export |

## 4. Configuration

| Variable | Default | Effect |
|---|---|---|
| `BACKEND_URL` | `http://127.0.0.1:8000` | Where `npm run dev` forwards `/api/**` |
| `ANALOG_EXPORT_DIR` | `../data/export` | Where `npm run sync-data` copies from |

`package.json` pins `@maplibre/geojson-vt` to 6.3.0 (`overrides`): version 6.3.1 ships type files that import its raw sources and break the typecheck.

## 5. Deployment

| Option | How | Gets you |
|---|---|---|
| **Vercel** (simplest) | Import the repository, set **Root Directory = `frontend`**, keep the defaults | The whole app as a static site |
| **Firebase Hosting, free plan** | `npm run build`, then `firebase deploy --only hosting --config firebase.static.json` | The same, on `https://PROJECT_ID.web.app` |
| **Firebase Hosting + Cloud Run** | Deploy the API ([backend/README.md](../backend/README.md#7-deployment)), then `npm run build` and `firebase deploy --only hosting` | Plus the API: weight sliders and team notes |

What the site shows is `public/data/`. To publish new results: `analog export` (it refreshes `public/data/`), commit, push or redeploy.

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| App shows "No data yet" | Run `analog demo` or `analog export` |
| App doesn't show new results | Click **Reload data** (the export copies to `frontend/public/data/` automatically) |
| Footer says "static copy" though the backend runs | The backend wasn't up when `npm run dev` started: restart `npm run dev` (or set `BACKEND_URL`) |
| `EADDRINUSE` when starting the app | An old server holds the port: stop it or use `npm run dev -- --port 3001` |
| Hydration warning with attributes like `bis_skin_checked` | A browser extension rewrote the page; try a private window |

## 7. Future work

### 7.1 A dynamic app backed by the API

Today the app is static: all results are JSON files copied into `public/data/` at build time, and new results need a redeploy. Next step: an app that asks the API for everything (see [backend/README.md](../backend/README.md#8-future-work)).

- **Load data on request:** fetch cells for the visible map area, target and criterion instead of whole region files. That allows many more regions, worldwide tiles and finer cells without a heavier site.
- **Save reviews on the server:** send field reviews to the API instead of keeping them in the browser and sharing a downloaded file. Everyone then sees new reviews and confidence indicators immediately.
- **Ranking over all regions:** "Where to test" across every region and world tile, computed by the API, not only the open region.
- **Live updates** after a new export or retraining, without rebuilding the site.

### 7.2 Field work

- **Photos and GPS points** attached to a review, and an observed value next to the satellite one (for example "slope on site: 8°" next to "slope from the DEM: 14°").
- **Offline use on site:** a phone-friendly mode (PWA) that caches the chosen region and syncs reviews when back online.
- **Export a field plan:** the selected candidates as GPX/KML with their criteria, to load in a GPS or a mapping app.

### 7.3 Exploring the results

- **Compare two targets or two criteria** side by side, or as a combined map.
- **Uncertainty on the map:** show how sure each score is (data coverage, model uncertainty, review confidence), not only the score.
- **An official imagery provider** with an API key (for example MapTiler or Mapbox) for a public deployment, instead of the unofficial Google and Bing tiles.
- **French and English** interface.
