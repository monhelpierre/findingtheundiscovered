"""Raster helpers: read any GeoTIFF, resample onto a region grid, terrain derivatives."""

from __future__ import annotations

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.warp import Resampling, reproject

WGS84 = CRS.from_epsg(4326)
RADIUS_M = {"earth": 6_371_008.8, "moon": 1_737_400.0, "mars": 3_389_500.0}


def read(path, band: int = 1):
    """Read one band as float64 with nodata converted to NaN."""
    with rasterio.open(path) as ds:
        a = ds.read(band, out_dtype="float64")
        nod = ds.nodata
        if nod is not None and not np.isnan(nod):
            a[a == nod] = np.nan
        return a, ds.transform, ds.crs


def to_grid(arr, transform, crs, region, resampling=Resampling.average) -> np.ndarray:
    """Resample an array onto the region's lon/lat cell grid. Cells with no data stay NaN."""
    h, w = region.shape
    dst = np.full((h, w), np.nan, dtype="float64")
    reproject(
        source=np.ascontiguousarray(arr, dtype="float64"),
        destination=dst,
        src_transform=transform,
        src_crs=crs or WGS84,
        src_nodata=np.nan,
        dst_transform=region.transform,
        dst_crs=WGS84,
        dst_nodata=np.nan,
        resampling=resampling,
    )
    return dst


def combine(arrays: list[np.ndarray]) -> np.ndarray:
    """Mosaic: the first valid value wins."""
    out = arrays[0].copy()
    for a in arrays[1:]:
        m = np.isnan(out)
        out[m] = a[m]
    return out


def files_to_grid(paths, region, resampling=Resampling.average) -> np.ndarray:
    return combine([to_grid(*read(p), region, resampling) for p in paths])


# ---- terrain -----------------------------------------------------------------

def pixel_size_m(transform, crs, shape, body: str = "earth"):
    """Pixel size in metres. Geographic grids use the body's radius; dx varies with latitude."""
    if crs is None or crs.is_geographic:
        mpd = np.pi * RADIUS_M[body] / 180.0
        lat = transform.f + (np.arange(shape[0]) + 0.5) * transform.e
        dx = abs(transform.a) * mpd * np.cos(np.radians(lat))[:, None]
        dy = abs(transform.e) * mpd
        return np.maximum(dx, 1e-6), float(dy)
    return float(abs(transform.a)), float(abs(transform.e))


GENTLE_SLOPE_DEG = 15.0  # operations-safe ground: slope between 0 and 15 degrees (team decision)


def gentle_fraction(s: np.ndarray) -> np.ndarray:
    """1 where a land pixel's slope is <= 15 deg, 0 where steeper, NaN where it isn't land.
    Water and ocean are perfectly flat in SRTM/NASADEM (their elevation was set to one value), so a
    slope of exactly 0 is treated as water, not as ideal flat ground."""
    out = (s <= GENTLE_SLOPE_DEG).astype("float64")
    out[~np.isfinite(s) | (s == 0)] = np.nan
    return out


def slope_deg(z: np.ndarray, dx, dy) -> np.ndarray:
    gy, gx = np.gradient(z)
    return np.degrees(np.arctan(np.hypot(gx / dx, gy / dy)))


def terrain_to_grid(dem_path, region) -> dict[str, np.ndarray]:
    """Per-cell terrain statistics from a DEM at native resolution."""
    z, tr, crs = read(dem_path)
    dx, dy = pixel_size_m(tr, crs, z.shape, "earth")
    s = slope_deg(z, dx, dy)
    d = z - np.nanmean(z)  # centred, so the variance below keeps float precision

    def g(a, rs=Resampling.average):
        return to_grid(a, tr, crs, region, rs)

    mean_d, mean_d2 = g(d), g(d * d)
    return {
        "elev_mean": g(z),
        "slope_mean": g(s),
        "slope_p75": g(s, Resampling.q3),
        "roughness": np.sqrt(np.maximum(mean_d2 - mean_d**2, 0.0)),
        "relief": g(z, Resampling.max) - g(z, Resampling.min),
        "gentle_slope_frac": g(gentle_fraction(s)),
    }


# ---- planetary target profiles ---------------------------------------------------

def _blocks(a: np.ndarray, b: int) -> np.ndarray:
    b = max(1, min(b, a.shape[0], a.shape[1]))
    h, w = (a.shape[0] // b) * b, (a.shape[1] // b) * b
    return a[:h, :w].reshape(h // b, b, w // b, b).swapaxes(1, 2).reshape(h // b, w // b, b * b)


def terrain_profile(dem_path, body: str, cell_m: float = 1000.0, pixel_m: float | None = None,
                    match_m: float = 30.0) -> dict:
    """Terrain profile of a Moon/Mars (or Earth) DEM clip using the same definitions as the
    Earth grid: per ~1 km block slope mean / p75, elevation std and max-min, then averaged.

    The DEM is block-averaged to ~match_m metres first, so slopes are comparable with SRTM (30 m).
    """
    z, tr, crs = read(dem_path)
    if pixel_m:
        px = float(pixel_m)
    else:
        dx, dy = pixel_size_m(tr, crs, z.shape, body)
        px = float(np.median(np.sqrt(np.asarray(dx, dtype=float) * dy)))
    warnings = []
    f = int(round(match_m / px))
    if f > 1:
        z = np.nanmean(_blocks(z, f), axis=-1)
        px *= f
    elif px > match_m * 1.5:
        warnings.append(
            f"DEM pixel is {px:.0f} m, coarser than SRTM's 30 m: slopes will be underestimated "
            "compared with Earth cells."
        )
    s = slope_deg(z, px, px)
    b = max(2, int(round(cell_m / px)))
    zb, sb = _blocks(z, b), _blocks(s, b)
    with np.errstate(all="ignore"), _quiet():
        stats = {
            "slope_mean": np.nanmean(sb, axis=-1),
            "slope_p75": np.nanpercentile(sb, 75, axis=-1),
            "roughness": np.nanstd(zb, axis=-1),
            "relief": np.nanmax(zb, axis=-1) - np.nanmin(zb, axis=-1),
            "gentle_slope_frac": np.nanmean(_blocks(gentle_fraction(s), b), axis=-1),
        }
    floors = {"slope_mean": 2.0, "slope_p75": 3.0, "roughness": 3.0, "relief": 15.0, "gentle_slope_frac": 0.05}
    out = {}
    for k, v in stats.items():
        v = v[np.isfinite(v)]
        if v.size == 0:
            continue
        out[k] = {
            "value": round(float(np.mean(v)), 3 if k == "gentle_slope_frac" else 2),
            "sigma": round(max(float(np.std(v)), floors[k]), 3 if k == "gentle_slope_frac" else 2),
            "weight": 1.0,
        }
    return {"features": out, "pixel_m": round(px, 2), "blocks": int(zb.shape[0] * zb.shape[1]),
            "warnings": warnings}


class _quiet:
    def __enter__(self):
        import warnings
        self._c = warnings.catch_warnings()
        self._c.__enter__()
        warnings.simplefilter("ignore", RuntimeWarning)

    def __exit__(self, *a):
        return self._c.__exit__(*a)
