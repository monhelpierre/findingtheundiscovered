"""Turn a region's registered layers into a per-cell feature table (features.parquet)."""

from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd

from .illumination import low_sun_days
from .rasterops import combine, files_to_grid, terrain_to_grid
from .region import Region

log = logging.getLogger(__name__)

# name -> (label, unit, description). Custom features are added at runtime.
FEATURES: dict[str, tuple[str, str, str]] = {
    "elev_mean": ("Elevation", "m", "Mean elevation (display only; absolute elevation is not comparable across bodies)"),
    "slope_mean": ("Mean slope", "deg", "Mean slope within the cell, from a ~30 m DEM"),
    "slope_p75": ("Steep slope (p75)", "deg", "75th percentile of slope within the cell"),
    "roughness": ("Roughness", "m", "Standard deviation of elevation within the cell"),
    "relief": ("Local relief", "m", "Max minus min elevation within the cell"),
    "gentle_slope_frac": ("Gentle ground (0-15 deg)", "fraction", "Share of the cell's land with a slope of 15 deg or less (water and ocean excluded): ground you can land, drive and build on"),
    "albedo": ("Albedo", "0-1", "Broadband surface albedo"),
    "ndvi": ("NDVI", "-1..1", "Vegetation index; Moon and Mars are ~0"),
    "temp_range": ("Temperature range", "K", "Diurnal surface temperature range"),
    "water_fraction": ("Water", "fraction", "Share of open water; cells that are mostly water are not scored"),
    "clay_index": ("Clay index (HLS)", "ratio", "SWIR1/SWIR2 band ratio: higher where clays and micas absorb near 2.2 um. Available everywhere, unlike EMIT"),
    "ferric_index": ("Ferric iron index (HLS)", "ratio", "Red/blue band ratio: higher on reddish, iron-oxide-rich ground. Available everywhere"),
    "min_mafic": ("Mafic minerals", "fraction", "Olivine / pyroxene: basalt plains, lava fields"),
    # Climate (Meteomatics, `analog fetch meteomatics`); names follow data/feature_schema.csv.
    "tmin_k": ("Lowest temperature", "K", "Lowest daily minimum air temperature (2 m) over the year"),
    "tday_range_k": ("Daily temperature range", "K", "Mean day-night range of air temperature (2 m)"),
    "tyear_range_k": ("Yearly temperature range", "K", "Warmest minus coldest monthly mean air temperature"),
    "sun_elev_deg": ("Noon sun elevation", "deg", "Mean noon sun elevation over the year (from latitude)"),
    "low_sun_days": ("Low-sun days", "days/yr", "Days a year when the noon Sun stays under 10 deg, as at the lunar south pole (from latitude; 0 below ~56 deg, ~107 at the Arctic Circle)"),
    # Earth Engine (backend/gee/analog_features.js), from the Artemis III Moon-base profile:
    "permafrost_class": ("Permafrost depth", "class", "3 frozen at the surface, 2 within 50 cm, 1 at 50-100 cm, 0 not frozen (ERA5-Land soil temperature: a layer whose warmest month stays below 0 C)"),
    "passable_2km": ("Passable ground, 2 km", "fraction", "Share of ground at 0-15 deg within 2 km: walking range of a spacesuit (xEVA)"),
    "passable_10km": ("Passable ground, 10 km", "fraction", "Share of ground at 0-15 deg within 10 km: range of an unpressurised rover (LTV)"),
    "passable_50km": ("Passable ground, 50 km", "fraction", "Share of ground at 0-15 deg within 50 km: range of a pressurised rover"),
    "hydrogen_or_soilmoist": ("Soil moisture", "index", "Mean soil moisture index at 5 cm (Earth's stand-in for Moon/Mars hydrogen)"),
    "ice_flag": ("Ice or frozen ground", "0 or 1", "1 if snow or frozen ground on at least 15 days a year"),
    "dust_aod": ("Dust", "AOD", "Mean dust aerosol optical depth at 550 nm"),
    "wind_ms": ("Wind speed", "m/s", "Mean daily wind speed at 10 m"),
    "aridity_index": ("Aridity index", "ratio", "Yearly precipitation / evapotranspiration (low = arid)"),
    "min_phyllosilicate": ("Clays", "fraction", "Phyllosilicates: smectite, kaolinite, chlorite, illite"),
    "min_sulfate": ("Sulfates", "fraction", "Gypsum, jarosite, alunite"),
    "min_carbonate": ("Carbonates", "fraction", "Calcite, dolomite"),
    "min_iron_oxide": ("Iron oxides", "fraction", "Hematite, goethite"),
}

META_COLS = ["cell", "row", "col", "lon", "lat"]
OUTPUT_COLS = {"best_target", "best_score", "analog_prob", "region", "label", "site"}


def feature_meta(columns) -> dict:
    out = {}
    for c in columns:
        if c in META_COLS or c in OUTPUT_COLS or "__" in c:
            continue
        label, unit, desc = FEATURES.get(c, (c.replace("_", " ").capitalize(), "", "Custom feature"))
        out[c] = {"label": label, "unit": unit, "description": desc}
    return out


def compute(region: Region) -> pd.DataFrame:
    t_start = time.perf_counter()
    h, w = region.shape
    log.info("Features for %s: %s cells (%d x %d), %d layer kind(s)", region.id, f"{h * w:,}", h, w,
             sum(1 for v in region.layers.values() if v))
    lon, lat = region.centers()
    rows, cols = np.mgrid[0:h, 0:w]
    data: dict[str, np.ndarray] = {}

    dems = region.layer_paths("dem")
    if dems:
        t0 = time.perf_counter()
        parts = [terrain_to_grid(p, region) for p in dems]
        for k in parts[0]:
            data[k] = combine([p[k] for p in parts])
        log.info("  dem -> terrain (slope, steep slope, gentle ground, roughness, relief, elevation) from %d file(s): %.1f s",
                 len(dems), time.perf_counter() - t0)

    for kind, paths in region.layers.items():
        if kind == "dem" or not paths:
            continue
        if kind.startswith("mineral:"):
            name = "min_" + kind.split(":", 1)[1]
        elif kind.startswith("custom:"):
            name = kind.split(":", 1)[1]
        elif kind == "water":
            name = "water_fraction"
        else:
            name = kind
        t0 = time.perf_counter()
        grid = files_to_grid(region.layer_paths(kind), region)
        # A feature already computed from the DEM (e.g. slope_mean) keeps its values; another source of
        # the same feature (e.g. Earth Engine's custom:slope_mean) only fills the cells it left empty.
        data[name] = combine([data[name], grid]) if name in data else grid
        log.info("  %-22s -> %-20s %d file(s), %5.1f%% of cells have data, %.1f s", kind, name, len(paths),
                 100 * float(np.isfinite(data[name]).mean()), time.perf_counter() - t0)

    if not data:
        raise SystemExit(f"Region {region.id!r} has no layers. Add some with `analog fetch` or `analog ingest`.")

    df = pd.DataFrame({
        "cell": np.arange(h * w), "row": rows.ravel(), "col": cols.ravel(),
        "lon": lon.ravel(), "lat": lat.ravel(),
        **{k: v.ravel() for k, v in data.items()},
    })
    feat_cols = list(data)
    df = df[df[feat_cols].notna().any(axis=1)].reset_index(drop=True)
    # Noon sun elevation is pure geometry (latitude), so every cell gets it, with or without climate data.
    from .meteomatics import sun_elevation_mean
    ulat, inv = np.unique(df["lat"].to_numpy(dtype=float), return_inverse=True)  # one value per grid row
    df["sun_elev_deg"] = np.round(sun_elevation_mean(ulat), 3)[inv]
    feat_cols = feat_cols if "sun_elev_deg" in feat_cols else [*feat_cols, "sun_elev_deg"]
    df["low_sun_days"] = low_sun_days(df["lat"].to_numpy(dtype=float))
    df.to_parquet(region.dir / "features.parquet", index=False)
    log.info("  wrote features.parquet: %s cells with data, %d feature(s), %.1f s", f"{len(df):,}",
             len(feat_cols), time.perf_counter() - t_start)
    return df


def load(region: Region) -> pd.DataFrame:
    p = region.dir / "features.parquet"
    if not p.exists():
        raise SystemExit(f"No features for {region.id!r}. Run: analog features {region.id}")
    df = pd.read_parquet(p)
    if "low_sun_days" not in df and "lat" in df:  # geometry only: older feature tables get it here
        df["low_sun_days"] = low_sun_days(df["lat"].to_numpy(dtype=float))
    return df
