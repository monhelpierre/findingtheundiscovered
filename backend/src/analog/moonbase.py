"""Moon-base sites from the sister project (Artemis III profile, whole Earth at 10 km), for the web app.

The sister project (NASA Space Apps 2026, Sherbrooke; MIT licence) scores every 10 km square of land
against the Artemis III Moon-base criteria with AHP weights, and groups the squares into sites of at
most 50 km. Its `data/processed/sites.parquet` (or `sites_moon.csv`) is converted here into a compact
column file that the app shows as an extra layer:

    analog moonbase <path to sites.parquet or sites_moon.csv>   -> data/export/moonbase.json

`analog export` keeps the file, and it is copied to the web app with the rest of the export.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import pandas as pd

from . import paths

log = logging.getLogger(__name__)

# column -> decimals (None: text). Only what the app shows.
COLUMNS: dict[str, int | None] = {
    "site_code": None, "country": None, "continent": None, "tier": None, "lat": 4, "lon": 4,
    "S": 3, "n_met": 0, "n_scored": 0, "s1": 2, "s2": 2, "s3": 2, "s4": 2, "s7": 2, "s8": 2,
    "low_sun_days": 0, "ice_label": None, "area_km2": 0, "best_on_continent": None, "rank": 0,
}
SOURCE = {
    "name": "Finding the Undiscovered: Moon base profile (Artemis III)",
    "method": "Whole Earth at 10 km; criteria scored 0-1 (met at 0.70), AHP weights illumination 0.467, "
              "water ice 0.277, terrain and mobility 0.160, regolith and soil 0.095; no vegetation, "
              "passable ground and ice-free land required; 50 km around 32 known analogs excluded; "
              "touching squares of the same tier grouped into sites of at most 50 km.",
    "criteria": {"s1": "Surface material (sand)", "s2": "Terrain (vs. Shackleton, LOLA 120 m)",
                 "s3": "Illumination (low-sun days)", "s4": "Water, ice (permafrost depth)",
                 "s7": "Subsurface (regolith depth)", "s8": "Mobility (passable ground 2/10/50 km)"},
}


def _val(v, nd):
    if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NA:
        return None
    if nd is None:
        return bool(v) if isinstance(v, bool) else str(v)
    return round(float(v), nd) if nd else int(round(float(v)))


def load_sites(path: Path) -> pd.DataFrame:
    path = Path(path)
    if path.is_dir():
        for name in ("data/processed/sites.parquet", "sites.parquet",
                     "data/processed/sites_moon.csv", "sites_moon.csv"):
            if (path / name).exists():
                path = path / name
                break
    if not path.is_file():
        raise SystemExit(f"No sites file at {path} (expected sites.parquet or sites_moon.csv).")
    df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    missing = {"lat", "lon", "tier", "S"} - set(df.columns)
    if missing:
        raise SystemExit(f"{path.name}: missing column(s) {', '.join(sorted(missing))}")
    if "synthetic" in df and df["synthetic"].fillna(False).astype(bool).any():
        raise SystemExit(f"{path.name} holds SYNTHETIC test sites, not results: run their pipeline on real data first.")
    return df


def convert(df: pd.DataFrame) -> dict:
    cols = [c for c in COLUMNS if c in df.columns]
    return {**SOURCE, "n": len(df),
            "columns": {c: [_val(v, COLUMNS[c]) for v in df[c].tolist()] for c in cols}}


def import_sites(path: Path) -> Path:
    data = convert(load_sites(path))
    paths.EXPORT.mkdir(parents=True, exist_ok=True)
    out = paths.EXPORT / "moonbase.json"
    out.write_text(json.dumps(data, separators=(",", ":")))
    tiers = pd.Series(data["columns"]["tier"]).value_counts().to_dict()
    log.info("Moon-base sites: %s, tiers %s -> %s (%.1f MB)", f"{data['n']:,}",
             ", ".join(f"{k} {v:,}" for k, v in sorted(tiers.items())), out, out.stat().st_size / 1e6)
    return out
