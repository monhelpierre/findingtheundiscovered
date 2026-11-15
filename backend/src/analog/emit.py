"""EMIT L2B mineral identification (netCDF swath) -> georeferenced 0/1 rasters per mineral group.

Each pixel is 1 if the group-1 (2 um features: clays, carbonates, sulfates) or group-2
(1 um features: iron oxides) mineral EMIT identified there belongs to the group *and* its band
depth is at least `min_band_depth`, 0 if it was observed but isn't a confident detection of that
group, and NaN if it wasn't observed. Averaging these onto the
region grid gives the fraction of observed pixels in each group.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import rasterio
import yaml
from affine import Affine

from . import paths


def load_group_config() -> dict:
    """Returns {"groups": {name: [regex...]}, "min_band_depth": float}."""
    doc = yaml.safe_load((paths.CONFIG / "mineral_groups.yaml").read_text(encoding="utf-8"))
    return {"groups": doc["groups"], "min_band_depth": float(doc.get("min_band_depth", 0.0))}


def classify_names(names: dict[int, str], groups: dict[str, list[str]]) -> dict[str, set[int]]:
    out = {g: set() for g in groups}
    for idx, name in names.items():
        low = name.lower()
        for g, keys in groups.items():
            if any(re.search(k, low) for k in keys):
                out[g].add(idx)
    return out


def _mineral_names(ds) -> dict[int, str]:
    """Mineral-id -> name table stored in the granule's mineral_metadata group."""
    meta = ds.groups.get("mineral_metadata")
    if meta is None:
        raise ValueError("EMIT file has no mineral_metadata group")
    var = next((meta.variables[k] for k in ("name", "mineral_name", "names") if k in meta.variables), None)
    if var is None:
        raise ValueError(f"No name variable in mineral_metadata (has: {list(meta.variables)})")
    raw = var[:]
    names = ["".join(c.decode() if isinstance(c, bytes) else str(c) for c in row).strip()
             if getattr(row, "ndim", 0) else (row.decode() if isinstance(row, bytes) else str(row))
             for row in raw]
    if "index" in meta.variables:
        idx = [int(i) for i in np.asarray(meta.variables["index"][:]).ravel()]
    else:
        idx = list(range(len(names)))
    return dict(zip(idx, names, strict=True))


def emit_to_group_rasters(nc_path: Path, out_dir: Path, cfg: dict):
    import netCDF4

    out_dir.mkdir(parents=True, exist_ok=True)
    with netCDF4.Dataset(nc_path) as ds:
        gt = [float(v) for v in ds.getncattr("geotransform")]
        loc = ds.groups["location"]
        glt_x = np.asarray(loc.variables["glt_x"][:].filled(0), dtype=np.int64)
        glt_y = np.asarray(loc.variables["glt_y"][:].filled(0), dtype=np.int64)
        ids1 = np.ma.filled(ds.variables["group_1_mineral_id"][:].astype("float64"), np.nan)
        ids2 = np.ma.filled(ds.variables["group_2_mineral_id"][:].astype("float64"), np.nan)
        bd1 = np.ma.filled(ds.variables["group_1_band_depth"][:].astype("float64"), np.nan)
        bd2 = np.ma.filled(ds.variables["group_2_band_depth"][:].astype("float64"), np.nan)
        names = _mineral_names(ds)

    valid = (glt_x > 0) & (glt_y > 0)
    oy, ox = glt_y[valid] - 1, glt_x[valid] - 1

    def ortho(a):
        out = np.full(glt_x.shape, np.nan)
        out[valid] = a[oy, ox]
        out[out < 0] = np.nan  # fill values (e.g. -9999): not observed
        return out

    m1, m2 = ortho(ids1), ortho(ids2)
    observed = np.isfinite(m1) | np.isfinite(m2)
    thr = cfg["min_band_depth"]
    with np.errstate(invalid="ignore"):
        m1[~(ortho(bd1) >= thr)] = -1  # weak or missing absorption: not a detection
        m2[~(ortho(bd2) >= thr)] = -1
    by_group = classify_names(names, cfg["groups"])
    transform = Affine.from_gdal(*gt)
    stem = nc_path.stem
    results = []
    for g, ids in by_group.items():
        if not ids:
            continue
        ids_arr = np.array(sorted(ids), dtype=float)
        hit = np.isin(m1, ids_arr) | np.isin(m2, ids_arr)
        arr = np.where(observed, hit.astype("float32"), np.nan).astype("float32")
        out = out_dir / f"{stem}_{g}.tif"
        with rasterio.open(out, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1,
                           dtype="float32", crs="EPSG:4326", transform=transform, nodata=np.nan,
                           compress="deflate") as dst:
            dst.write(arr, 1)
        results.append((g, out))
    return results
