"""Meteomatics Weather API -> yearly climate statistics per region (criteria 3, 4, 5 and 6).

Space Apps participants get free API access for the duration of the challenge, so the raw
responses are cached under data/regions/<id>/raw/meteomatics/ and later runs use the cache
(no request). One request per month per region: a ~0.05 deg grid x the month's days x 9 daily
parameters; a full year for one region is 12 requests (the account allows 1,000 per day).

Credentials: METEOMATICS_USERNAME / METEOMATICS_PASSWORD, or <repo>/meteomatics/credentials.md
("Username: ..." and "Password: ..." lines; that folder is git-ignored). Never logged.

Features written (feature name = team schema column, data/feature_schema.csv):
    tmin_k                lowest daily minimum air temperature at 2 m over the year (K)        3
    tday_range_k          mean daily range of 2 m air temperature (K)                          3
    tyear_range_k         warmest minus coldest monthly mean 2 m temperature (K)               3
    sun_elev_deg          mean noon sun elevation over the year (deg; from latitude, no request) 3
    hydrogen_or_soilmoist mean soil moisture index at 5 cm (Earth's stand-in for hydrogen)     4
    ice_flag              1 if snow or frozen ground on >= 15 days of the year, else 0         4
    dust_aod              mean dust aerosol optical depth at 550 nm                            5
    wind_ms               mean daily wind speed at 10 m (m/s)                                  5
    aridity_index         yearly precipitation / yearly evapotranspiration (dossier only)      6
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from . import paths
from .region import Region

log = logging.getLogger(__name__)

API = "https://api.meteomatics.com"
PARAMS = {  # API parameter -> short name
    "t_min_2m_24h:C": "tmin", "t_max_2m_24h:C": "tmax", "wind_speed_mean_10m_24h:ms": "wind",
    "dust_aod_550nm:idx": "dust", "soil_moisture_index_-5cm:idx": "soil", "precip_24h:mm": "precip",
    "evapotranspiration_24h:mm": "et", "snow_depth:cm": "snow", "frost_depth:cm": "frost",
}
FEATURES = ["tmin_k", "tday_range_k", "tyear_range_k", "sun_elev_deg", "hydrogen_or_soilmoist",
            "ice_flag", "dust_aod", "wind_ms", "aridity_index"]
INVALID = -600.0  # Meteomatics marks missing data with -666 / -999
ICE_DAYS = 15


def credentials() -> tuple[str, str]:
    user, pw = os.environ.get("METEOMATICS_USERNAME"), os.environ.get("METEOMATICS_PASSWORD")
    if user and pw:
        return user, pw
    f = paths.ROOT / "meteomatics" / "credentials.md"
    if f.exists():
        text = f.read_text(encoding="utf-8")
        mu, mp = re.search(r"Username:\s*(\S+)", text), re.search(r"Password:\s*(\S+)", text)
        if mu and mp:
            return mu.group(1), mp.group(1)
    raise SystemExit("No Meteomatics credentials: set METEOMATICS_USERNAME and METEOMATICS_PASSWORD, or put "
                     "'Username: ...' and 'Password: ...' lines in meteomatics/credentials.md.")


def grid_spec(bbox, res: float):
    """Grid points covering the bbox, north-west first (Meteomatics 'lat1,lon1_lat2,lon2:res')."""
    w, s, e, n = bbox
    ny = int(round((n - s) / res)) + 1
    nx = int(round((e - w) / res)) + 1
    lats = n - np.arange(ny) * res
    lons = w + np.arange(nx) * res
    spec = f"{n:.4f},{w:.4f}_{lats[-1]:.4f},{lons[-1]:.4f}:{res},{res}"
    return spec, lats, lons


def _get(url: str, auth: str, retries: int = 3) -> str:
    req = urllib.request.Request(url, headers={"Authorization": auth, "User-Agent": "analog-pipeline"})
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")
            msg = re.sub(r'"user"\s*:\s*"[^"]*"', '"user":"<hidden>"', msg)[:300]
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                log.warning("  Meteomatics HTTP %s, retrying in %d s", e.code, 10 * attempt)
                time.sleep(10 * attempt)
                continue
            raise SystemExit(f"Meteomatics request failed (HTTP {e.code}): {msg}") from None
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(10 * attempt)
                continue
            raise SystemExit(f"Meteomatics unreachable: {e.reason}") from None
        except TimeoutError:
            if attempt < retries:
                log.warning("  Meteomatics read timed out, retrying in %d s", 10 * attempt)
                time.sleep(10 * attempt)
                continue
            raise SystemExit("Meteomatics timed out (request too large? use a coarser --res)") from None
    raise SystemExit("Meteomatics request failed")


def fetch_month(region: Region, year: int, month: int, res: float, auth: str | None) -> Path:
    """Download (or reuse) one month of daily values for the region's grid."""
    raw = region.dir / "raw" / "meteomatics"
    raw.mkdir(parents=True, exist_ok=True)
    out = raw / f"{year}-{month:02d}_{res}.json"
    if out.exists() and out.stat().st_size > 0:
        return out
    if auth is None:
        raise SystemExit("Not cached and no credentials")
    spec, _, _ = grid_spec(region.bbox, res)
    # 24 h values are stamped at the end of their day, so the month runs from day 2 00:00 to the
    # 1st of the next month 00:00.
    start = f"{year}-{month:02d}-02T00:00:00Z"
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    end = f"{ny}-{nm:02d}-01T00:00:00Z"
    url = f"{API}/{start}--{end}:P1D/{','.join(PARAMS)}/{spec}/json"
    t0 = time.perf_counter()
    body = _get(url, auth)
    out.write_text(body, encoding="utf-8")
    log.info("  %d-%02d: downloaded %.1f MB in %.1f s", year, month, len(body) / 1e6, time.perf_counter() - t0)
    return out


def _parse(files: list[Path], lats, lons) -> dict[str, np.ndarray]:
    """{short name: array (days, ny, nx)} from the cached monthly responses (missing -> NaN)."""
    lat_i = {round(float(v), 4): i for i, v in enumerate(lats)}
    lon_i = {round(float(v), 4): i for i, v in enumerate(lons)}
    series: dict[str, list[np.ndarray]] = {k: [] for k in PARAMS.values()}
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        for block in doc["data"]:
            name = PARAMS.get(block["parameter"])
            if name is None:
                continue
            ndays = len(block["coordinates"][0]["dates"])
            arr = np.full((ndays, len(lats), len(lons)), np.nan)
            for c in block["coordinates"]:
                i, j = lat_i.get(round(c["lat"], 4)), lon_i.get(round(c["lon"], 4))
                if i is None or j is None:
                    continue
                vals = np.array([d["value"] if d["value"] is not None else np.nan for d in c["dates"]], float)
                vals[vals <= INVALID] = np.nan
                arr[:, i, j] = vals
            series[name].append(arr)
    return {k: np.concatenate(v, axis=0) for k, v in series.items() if v}


def sun_elevation_mean(lats: np.ndarray) -> np.ndarray:
    """Mean noon sun elevation over a year (deg), from latitude; 0 during polar night."""
    doy = np.arange(1, 366)
    decl = 23.44 * np.sin(np.radians(360.0 / 365.0 * (doy - 81)))
    elev = 90.0 - np.abs(lats[:, None] - decl[None, :])
    return np.clip(elev, 0, 90).mean(axis=1)


def statistics(s: dict[str, np.ndarray], lats: np.ndarray, nx: int) -> dict[str, np.ndarray]:
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            tmin, tmax = s["tmin"], s["tmax"]
            tmean = (tmin + tmax) / 2
            n = tmean.shape[0]
            month_edges = np.linspace(0, n, 13).astype(int)
            months = zip(month_edges[:-1], month_edges[1:], strict=True)
            monthly = np.stack([np.nanmean(tmean[a:b], axis=0) for a, b in months])
            frozen = (np.nan_to_num(s["snow"], nan=0) > 0) | (np.nan_to_num(s["frost"], nan=0) > 0)
            observed = np.isfinite(s["snow"]) | np.isfinite(s["frost"])
            # ICE_DAYS per year; with sampled days (global reference) the same share of the days
            ice_days = ICE_DAYS if n >= 360 else ICE_DAYS * n / 365
            precip, et = np.nansum(s["precip"], axis=0), np.nansum(s["et"], axis=0)
            out = {
                "tmin_k": np.nanmin(tmin, axis=0) + 273.15,
                "tday_range_k": np.nanmean(tmax - tmin, axis=0),
                "tyear_range_k": np.nanmax(monthly, axis=0) - np.nanmin(monthly, axis=0),
                "sun_elev_deg": np.repeat(sun_elevation_mean(lats)[:, None], nx, axis=1),
                "hydrogen_or_soilmoist": np.nanmean(s["soil"], axis=0),
                "ice_flag": np.where(observed.any(axis=0), (frozen.sum(axis=0) >= ice_days).astype(float), np.nan),
                "dust_aod": np.nanmean(s["dust"], axis=0),
                "wind_ms": np.nanmean(s["wind"], axis=0),
                "aridity_index": np.where(et > 0.1, precip / et, np.nan),
            }
    return out


def fetch_region(region: Region, year: int = 2024, res: float = 0.05, offline: bool = False) -> list[Path]:
    """One year of Meteomatics data for a region -> 9 feature layers (custom:<feature>)."""
    spec, lats, lons = grid_spec(region.bbox, res)
    auth = None
    if not offline:
        user, pw = credentials()
        auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    log.info("Meteomatics %s: %d x %d grid points (%.2f deg), year %d, %d parameters",
             region.id, len(lats), len(lons), res, year, len(PARAMS))
    files = [fetch_month(region, year, m, res, auth) for m in range(1, 13)]
    stats = statistics(_parse(files, lats, lons), lats, len(lons))

    layer_dir = region.dir / "layers"
    layer_dir.mkdir(parents=True, exist_ok=True)
    for name in FEATURES:  # replace earlier Meteomatics layers
        region.layers.pop(f"custom:{name}", None)
    transform = from_origin(lons[0] - res / 2, lats[0] + res / 2, res, res)
    outs = []
    for name in FEATURES:
        arr = stats[name].astype("float32")
        out = layer_dir / f"mm_{name}.tif"
        with rasterio.open(out, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1,
                           dtype="float32", crs="EPSG:4326", transform=transform, nodata=np.nan,
                           compress="deflate") as dst:
            dst.write(arr, 1)
        region.add_layer(f"custom:{name}", out)
        outs.append(out)
        v = arr[np.isfinite(arr)]
        log.info("  %-22s %s", name, f"{v.min():.3g} .. {v.max():.3g} (mean {v.mean():.3g})" if v.size else "no data")
    return outs


# ---- global reference (config/earth_reference.json) ------------------------------------------------

GLOBAL_BBOX = (-180.0, -56.0, 175.0, 72.0)  # land between 56 S and 72 N, like the rest of the pipeline
SAMPLE_DAYS = (1, 9, 17, 25)  # a global daily year is ~75 min of requests; 4 days a month is ~10 min


def fetch_global(year: int = 2024, res: float = 5.0, offline: bool = False) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """The 9 climate features on a coarse global grid, land only, for Earth's percentile reference.
    Returns {feature: (values, cos-latitude area weights)}. 12 requests (one per month, 4 days each);
    cached in data/earth_reference/meteomatics/. Land = cells with a soil moisture value (the ocean has none).
    Sampling 4 days a month makes `tmin_k` (yearly minimum) slightly less extreme than with every day."""
    raw = paths.DATA / "earth_reference" / "meteomatics"
    raw.mkdir(parents=True, exist_ok=True)
    spec, lats, lons = grid_spec(GLOBAL_BBOX, res)
    auth = None
    if not offline:
        user, pw = credentials()
        auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    log.info("Meteomatics global reference: %d x %d points (%.1f deg), %d, days %s of each month",
             len(lats), len(lons), res, year, "/".join(map(str, SAMPLE_DAYS)))
    files = []
    for month in range(1, 13):
        out = raw / f"{year}-{month:02d}_{res}_sampled.json"
        if not (out.exists() and out.stat().st_size > 0):
            if auth is None:
                raise SystemExit(f"{out.name} is not cached; run without --offline")
            # 24 h values are stamped at the end of their day
            stamps = [(np.datetime64(f"{year}-{month:02d}-{d:02d}") + np.timedelta64(1, "D")).astype(str) + "T00:00:00Z"
                      for d in SAMPLE_DAYS]
            url = f"{API}/{','.join(stamps)}/{','.join(PARAMS)}/{spec}/json"
            t0 = time.perf_counter()
            body = _get(url, auth)
            out.write_text(body, encoding="utf-8")
            log.info("  %d-%02d: downloaded %.1f MB in %.1f s", year, month, len(body) / 1e6, time.perf_counter() - t0)
        files.append(out)
    series = _parse(files, lats, lons)
    stats = statistics(series, lats, len(lons))
    land = np.isfinite(series["soil"]).any(axis=0)
    weights = np.repeat(np.cos(np.radians(lats))[:, None], len(lons), axis=1)
    log.info("  land points: %d of %d", int(land.sum()), land.size)
    return {name: (stats[name][land], weights[land]) for name in FEATURES}
