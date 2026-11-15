"""A synthetic region for testing the pipeline and UI without downloads.

It sits at 0-0.6 deg N/E (open ocean, "Null Island") so it can't be mistaken for a real place.
"""

from __future__ import annotations

import json

import numpy as np
import rasterio
from rasterio.transform import from_origin

from . import paths
from .region import Region

DEMO_ID = "demo-synthetic"
BBOX = [0.0, 0.0, 0.6, 0.6]
PX = 1 / 3600  # 1 arc-second, like SRTM


def _smooth_noise(rng, shape, scale):
    """Cheap fractal noise: upsampled random grids at several scales."""
    out = np.zeros(shape)
    for k, amp in ((8, 1.0), (24, 0.5), (72, 0.25), (216, 0.12)):
        g = rng.standard_normal((k + 1, k + 1))
        yi = np.linspace(0, k, shape[0])
        xi = np.linspace(0, k, shape[1])
        y0, x0 = np.floor(yi).astype(int).clip(0, k - 1), np.floor(xi).astype(int).clip(0, k - 1)
        fy, fx = (yi - y0)[:, None], (xi - x0)[None, :]
        a = g[y0][:, x0] * (1 - fy) * (1 - fx) + g[y0 + 1][:, x0] * fy * (1 - fx) \
            + g[y0][:, x0 + 1] * (1 - fy) * fx + g[y0 + 1][:, x0 + 1] * fy * fx
        out += amp * a
    return out * scale


def _write(path, arr, transform):
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1,
                       dtype="float32", crs="EPSG:4326", transform=transform, nodata=np.nan,
                       compress="deflate") as dst:
        dst.write(arr.astype("float32"), 1)


def build(seed: int = 7) -> Region:
    rng = np.random.default_rng(seed)
    n = int(round((BBOX[2] - BBOX[0]) / PX))
    y, x = np.mgrid[0:n, 0:n] / n  # 0..1, y=0 at north
    tr = from_origin(BBOX[0], BBOX[3], PX, PX)

    # Terrain: flat plain in the south-west, rugged massif in the north-east, one crater.
    massif = np.clip((x + (1 - y) - 1.0) * 2.0, 0, 1)
    dem = 800 + 1200 * massif**1.5 + _smooth_noise(rng, (n, n), 25 + 220 * massif)
    r = np.hypot(x - 0.3, y - 0.3)
    dem += np.where(r < 0.08, -180 * (1 - (r / 0.08) ** 2), 0) + 60 * np.exp(-((r - 0.085) / 0.01) ** 2)
    # Basin in the south-east.
    dem -= 120 * np.exp(-(((x - 0.8) / 0.12) ** 2 + ((y - 0.8) / 0.12) ** 2))

    veg = np.clip(0.6 * np.exp(-(((x - 0.15) / 0.12) ** 2 + ((y - 0.85) / 0.1) ** 2)), 0, 1)
    ndvi = 0.04 + veg + 0.02 * _smooth_noise(rng, (n, n), 1)
    albedo = 0.32 - 0.14 * massif + 0.04 * _smooth_noise(rng, (n, n), 1) - 0.1 * veg
    basin = np.exp(-(((x - 0.8) / 0.15) ** 2 + ((y - 0.8) / 0.15) ** 2))
    p_sulfate = 0.7 * basin
    p_clay = 0.5 * np.exp(-(((x - 0.3) / 0.1) ** 2 + ((y - 0.3) / 0.1) ** 2))
    p_feox = 0.35 * np.clip(1 - massif, 0, 1) * (1 - basin)
    p_mafic = 0.7 * massif
    u = rng.random((4, n, n))

    if (paths.REGIONS / DEMO_ID).exists():
        import shutil
        shutil.rmtree(paths.REGIONS / DEMO_ID)
    region = Region.create(DEMO_ID, BBOX, 0.01, "Synthetic demo (not real data)")
    ld = region.dir / "layers"
    ld.mkdir(parents=True, exist_ok=True)
    layers = {
        "dem": dem, "albedo": albedo, "ndvi": ndvi,
        "mineral:sulfate": (u[0] < p_sulfate).astype(float),
        "mineral:phyllosilicate": (u[1] < p_clay).astype(float),
        "mineral:iron_oxide": (u[2] < p_feox).astype(float),
        "mineral:mafic": (u[3] < p_mafic).astype(float),
    }
    for kind, arr in layers.items():
        p = ld / f"{kind.replace(':', '_')}.tif"
        _write(p, arr, tr)
        region.add_layer(kind, p)

    # Demo labels for the classifier (synthetic, same fake location).
    paths.LABELS.mkdir(parents=True, exist_ok=True)

    def box(w, s, e, nn):
        return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, nn], [w, nn], [w, s]]]}

    labels = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "Demo massif A", "analog_type": "lunar_highland_terrain"},
         "geometry": box(0.47, 0.47, 0.55, 0.55)},
        {"type": "Feature", "properties": {"name": "Demo massif B", "analog_type": "lunar_highland_terrain"},
         "geometry": box(0.36, 0.52, 0.42, 0.58)},
        {"type": "Feature", "properties": {"name": "Demo basin", "analog_type": "mars_sulfate"},
         "geometry": box(0.44, 0.08, 0.52, 0.16)},
        {"type": "Feature", "properties": {"name": "Demo basin edge", "analog_type": "mars_sulfate"},
         "geometry": box(0.36, 0.12, 0.42, 0.18)},
    ]}
    (paths.LABELS / "demo_labels.geojson").write_text(json.dumps(labels, indent=1))
    return region
