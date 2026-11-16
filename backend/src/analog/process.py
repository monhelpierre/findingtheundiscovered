"""Turn local data files into region layers. Used both after downloading (fetch.py) and for
files you already have (importer.py), so the two paths produce identical results.

Every function takes a region and a list of local files, writes GeoTIFF layers into
<region>/layers/ and registers them in region.json.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import numpy as np
import rasterio
import rasterio.warp
import rasterio.windows
from rasterio.merge import merge

from .emit import emit_to_group_rasters, load_group_config
from .region import Region

# ---- footprints (lon/lat bounds of a file, to match it to regions) -------------------

SRTM_NAME = re.compile(r"^([NS])(\d{2})([EW])(\d{3})(?:\.SRTMGL1)?\.hgt(?:\.zip)?$", re.I)
HLS_NAME = re.compile(r"^(HLS\.(L30|S30)\.(T\w{5})\.(\d{7})T\d{6}\.v[\d.]+)\.(\w+)\.tif$", re.I)
EMIT_NAME = re.compile(r"^EMIT_L2B_MIN_\d{3}_\d{8}T\d{6}.*\.nc$", re.I)


def srtm_bounds(name: str):
    m = SRTM_NAME.match(name)
    if not m:
        return None
    lat = int(m[2]) * (1 if m[1].upper() == "N" else -1)
    lon = int(m[4]) * (1 if m[3].upper() == "E" else -1)
    return (lon, lat, lon + 1, lat + 1)


def raster_bounds(path: Path):
    """lon/lat bounds of any raster GDAL can open, or None."""
    try:
        with rasterio.open(path) as ds:
            if ds.crs is None:
                return None
            return rasterio.warp.transform_bounds(ds.crs, "EPSG:4326", *ds.bounds)
    except Exception:
        return None


def emit_bounds(path: Path):
    import netCDF4
    try:
        with netCDF4.Dataset(path) as ds:
            gt = [float(v) for v in ds.getncattr("geotransform")]
            h, w = ds.groups["location"].variables["glt_x"].shape
        x0, dx, _, y0, _, dy = gt
        return (x0, y0 + h * dy, x0 + w * dx, y0)
    except Exception:
        return None


def intersects(a, b) -> bool:
    return a is not None and b is not None and a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


# ---- SRTM ------------------------------------------------------------------------------

def process_srtm(region: Region, files: list[Path]) -> list[Path]:
    """SRTM tiles (.hgt, .hgt.zip or GeoTIFF) -> one DEM mosaic clipped to the region."""
    w, s, e, n = region.bbox
    raw = region.dir / "raw" / "srtm"
    raw.mkdir(parents=True, exist_ok=True)
    tiles = []
    for f in map(Path, files):
        if f.name.lower().endswith(".zip"):
            with zipfile.ZipFile(f) as z:
                for name in z.namelist():
                    if name.lower().endswith(".hgt"):
                        z.extract(name, raw)
                        tiles.append(raw / name)
        elif f.suffix.lower() in {".hgt", ".tif", ".tiff"}:
            tiles.append(f)
    tiles = [t for t in tiles if intersects(srtm_bounds(t.name) or raster_bounds(t), region.bbox)]
    # The same tile may arrive as .hgt and .hgt.zip: keep one per tile name.
    uniq = {}
    for t in tiles:
        uniq.setdefault(t.name.split(".")[0].upper(), t)
    tiles = list(uniq.values())
    if not tiles:
        raise SystemExit(f"No SRTM tiles overlap region {region.id}.")
    srcs = [rasterio.open(p) for p in tiles]
    try:
        pad = 0.01
        mosaic, tr = merge(srcs, bounds=(w - pad, s - pad, e + pad, n + pad), nodata=-32768)
        crs = srcs[0].crs
    finally:
        for sr in srcs:
            sr.close()
    profile = dict(driver="GTiff", height=mosaic.shape[1], width=mosaic.shape[2], transform=tr, crs=crs,
                   count=1, dtype="int16", nodata=-32768, compress="deflate", tiled=True,
                   blockxsize=256, blockysize=256)
    out = region.dir / "layers" / "srtm_dem.tif"
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(mosaic[0].astype("int16"), 1)
    region.layers.pop("dem", None)
    region.add_layer("dem", out)
    print(f"  SRTM: {len(tiles)} tile(s) -> {out.name}")
    return [out]


# ---- HLS -------------------------------------------------------------------------------

# Band roles for the two halves of HLS (Landsat L30, Sentinel-2 S30).
HLS_ROLES = {
    "L30": {"blue": "B02", "red": "B04", "nir": "B05", "swir1": "B06", "swir2": "B07", "fmask": "Fmask"},
    "S30": {"blue": "B02", "red": "B04", "nir": "B8A", "swir1": "B11", "swir2": "B12", "fmask": "Fmask"},
}
HLS_LAYERS = ("albedo", "ndvi", "water", "clay_index", "ferric_index")
FMASK_CLOUDY = 0b001110  # cloud(1), adjacent to cloud/shadow(2), cloud shadow(3)
FMASK_WATER = 0b100000  # water(5)
# Fmask often flags very dark rock (fresh lava) as water. Real water reflects almost no
# shortwave infrared: open sea and lakes measured < 0.015 in SWIR1, even with sun glint,
# while Lanzarote's dark lava measured 0.015-0.026. So "water" = Fmask water AND SWIR1 < this.
WATER_SWIR1_MAX = 0.015


def hls_band_names(product: str) -> list[str]:
    return list(HLS_ROLES[product.upper()].values())


def group_hls(files: list[Path]) -> dict[str, dict]:
    """Group HLS band files by scene: {scene_id: {product, tile, date, bands: {band: path}}}."""
    scenes: dict[str, dict] = {}
    for f in files:
        m = HLS_NAME.match(Path(f).name)
        if not m:
            continue
        sid, product, tile, date, band = m[1], m[2].upper(), m[3], m[4], m[5]
        sc = scenes.setdefault(sid, {"product": product, "tile": tile, "date": date, "bands": {}})
        sc["bands"][band if band != "fmask" else "Fmask"] = Path(f)
    return scenes


def _hls_scene_arrays(scene: dict):
    roles = HLS_ROLES[scene["product"]]
    with rasterio.open(scene["bands"][roles["fmask"]]) as fm:
        fmask = fm.read(1)
        profile = fm.profile.copy()
    bands = {}
    for role, band in roles.items():
        if role == "fmask":
            continue
        with rasterio.open(scene["bands"][band]) as ds:
            a = ds.read(1).astype("float32")
            a[a == -9999] = np.nan
            bands[role] = a * 0.0001
    return fmask, bands, profile


def process_hls_scene(scene: dict, out_stem: Path) -> dict[str, Path]:
    """One HLS scene -> albedo, NDVI, water and two mineral indices (NaN where cloudy/no data)."""
    fmask, b, profile = _hls_scene_arrays(scene)
    cloudy = (fmask & FMASK_CLOUDY) > 0
    nodata = ~np.isfinite(b["swir1"])
    with np.errstate(invalid="ignore"):
        is_water = ((fmask & FMASK_WATER) > 0) & (b["swir1"] < WATER_SWIR1_MAX)
    water = is_water.astype("float32")
    water[cloudy | nodata] = np.nan
    for k in b:
        b[k][cloudy | is_water] = np.nan
    # Liang (2001) shortwave albedo for Landsat-type bands: blue, red, NIR, SWIR1, SWIR2.
    albedo = (0.356 * b["blue"] + 0.130 * b["red"] + 0.373 * b["nir"]
              + 0.085 * b["swir1"] + 0.072 * b["swir2"] - 0.0018)
    albedo = np.clip(albedo, 0.0, 1.0)  # the -0.0018 offset can push near-black pixels below 0
    with np.errstate(invalid="ignore", divide="ignore"):
        denom = b["nir"] + b["red"]
        ndvi = (b["nir"] - b["red"]) / denom
        # Very dark pixels make the ratio explode (we saw -19..+19); NDVI is only defined in [-1, 1].
        ndvi[~(denom > 0.02)] = np.nan
        ndvi = np.clip(ndvi, -1.0, 1.0)
        # Classic band-ratio mineral indicators, available everywhere (unlike EMIT):
        #   clay / hydroxyl index = SWIR1 / SWIR2  (clays and micas absorb near 2.2 um, in SWIR2)
        #   ferric iron index     = red / blue     (iron oxides make ground red)
        clay = b["swir1"] / b["swir2"]
        clay[~(b["swir2"] > 0.01)] = np.nan
        ferric = b["red"] / b["blue"]
        ferric[~(b["blue"] > 0.01)] = np.nan
    profile.update(dtype="float32", nodata=np.nan, compress="deflate", count=1, driver="GTiff")
    outs = {}
    for name, arr in (("albedo", albedo), ("ndvi", ndvi), ("water", water),
                      ("clay_index", np.clip(clay, 0, 5)), ("ferric_index", np.clip(ferric, 0, 10))):
        out = out_stem.with_name(f"{out_stem.name}_{name}.tif")
        with rasterio.open(out, "w", **profile) as dst:
            dst.write(arr.astype("float32"), 1)
        outs[name] = out
    return outs


def _clear_fraction(scene: dict, bbox) -> float:
    """Share of the region's pixels that are observed and cloud-free in this scene."""
    roles = HLS_ROLES[scene["product"]]
    try:
        with rasterio.open(scene["bands"][roles["fmask"]]) as fm:
            b = rasterio.warp.transform_bounds("EPSG:4326", fm.crs, *bbox)
            win = rasterio.windows.from_bounds(*b, fm.transform)
            f = fm.read(1, window=win, boundless=True, fill_value=255)
    except Exception:
        return 0.0
    return float(((f != 255) & ((f & FMASK_CLOUDY) == 0)).mean()) if f.size else 0.0


def process_hls(region: Region, files: list[Path], scenes_per_tile: int = 2,
                ranking: dict[str, float] | None = None, replace: bool = True) -> list[Path]:
    """HLS band files -> region layers. Per MGRS tile, the best `scenes_per_tile` scenes are used;
    later scenes only fill gaps in earlier ones. `ranking` (scene id -> score, higher = better)
    comes from the search metadata when downloading; for local files we rank scenes by their
    cloud-free share of the region."""
    scenes = group_hls(files)
    complete = {}
    for sid, sc in scenes.items():
        need = hls_band_names(sc["product"])
        missing = [b for b in need if b not in sc["bands"]]
        if missing:
            print(f"  skip {sid}: missing bands {missing}")
        else:
            complete[sid] = sc
    if not complete:
        raise SystemExit("No complete HLS scenes (each needs blue, red, NIR, SWIR1, SWIR2 and Fmask).")
    complete = {sid: sc for sid, sc in complete.items()
                if intersects(raster_bounds(next(iter(sc["bands"].values()))), region.bbox)}
    if not complete:
        raise SystemExit(f"None of the HLS scenes overlap region {region.id}.")
    if ranking is None:
        ranking = {sid: _clear_fraction(sc, region.bbox) for sid, sc in complete.items()}
    by_tile: dict[str, list[str]] = {}
    for sid, sc in complete.items():
        by_tile.setdefault(sc["tile"], []).append(sid)

    layer_dir = region.dir / "layers"
    layer_dir.mkdir(parents=True, exist_ok=True)
    if replace:
        for k in HLS_LAYERS:
            region.layers.pop(k, None)
        for old in layer_dir.glob("hls_*.tif"):
            old.unlink()
    outs = []
    for tile, sids in sorted(by_tile.items()):
        sids.sort(key=lambda s: -ranking.get(s, 0))
        for sid in sids[:scenes_per_tile]:
            sc = complete[sid]
            print(f"  HLS {sc['product']} {tile} {sc['date']} (rank score {ranking.get(sid, 0):.2f})")
            stem = layer_dir / f"hls_{tile}_{sc['date']}_{sc['product'].lower()}"
            for name, path in process_hls_scene(sc, stem).items():
                region.add_layer(name, path)
                outs.append(path)
    return outs


# ---- EMIT -----------------------------------------------------------------------------

def process_emit(region: Region, files: list[Path], replace: bool = True) -> list[Path]:
    files = [Path(f) for f in files if EMIT_NAME.match(Path(f).name) and "UNCERT" not in Path(f).name.upper()]
    files = [f for f in files if intersects(emit_bounds(f), region.bbox)]
    # The same acquisition can exist in several collection versions (001, 002): keep the newest.
    newest: dict[str, Path] = {}
    for f in files:
        parts = f.name.split("_")  # EMIT_L2B_MIN_<ver>_<datetime>[_...]
        key = parts[4] if len(parts) > 4 else f.name
        if key not in newest or parts[3] > newest[key].name.split("_")[3]:
            newest[key] = f
    files = list(newest.values())
    if not files:
        raise SystemExit(f"No EMIT L2B mineral files overlap region {region.id}.")
    cfg = load_group_config()
    if replace:
        for k in [k for k in region.layers if k.startswith("mineral:")]:
            region.layers.pop(k)
    outs = []
    for f in files:
        for group, path in emit_to_group_rasters(f, region.dir / "layers", cfg):
            region.add_layer(f"mineral:{group}", path)
            outs.append(path)
    print(f"  EMIT: {len(files)} granule(s) -> {len(outs)} mineral-group raster(s)")
    return outs


# ---- Google Earth Engine feature exports ------------------------------------------------
# backend/gee/analog_features.js exports one multi-band GeoTIFF per site (or several tiles for
# the whole Earth), named analogfeat_<site>[-<tile offsets>].tif, with the bands in this order.
# Keep this list identical to BANDS in the script. GeoTIFF exports from Earth Engine don't
# keep band names, so the order is the contract (band descriptions are used when present).

GEE_NAME = re.compile(r"^analogfeat_.+\.tif$", re.I)
GEE_BANDS = ["elev_mean", "slope_mean", "slope_p75", "roughness", "relief", "albedo", "ndvi",
             "water_fraction", "temp_range", "clay_index", "ferric_index", "gentle_slope_frac",
             "permafrost_class", "passable_2km", "passable_10km", "passable_50km"]
GEE_BANDS_V2 = GEE_BANDS[:12]  # files exported before the Moon-base bands (permafrost, passable ground)
GEE_BANDS_V1 = GEE_BANDS[:11]  # files exported before gentle_slope_frac was added
# Band -> region layer kind. Terrain statistics are already per-cell values, so they enter as
# custom:<feature> layers (averaged onto the grid like any other layer).
GEE_KIND = {
    "elev_mean": "custom:elev_mean", "slope_mean": "custom:slope_mean", "slope_p75": "custom:slope_p75",
    "roughness": "custom:roughness", "relief": "custom:relief", "albedo": "albedo", "ndvi": "ndvi",
    "water_fraction": "water", "temp_range": "temp_range", "clay_index": "clay_index",
    "ferric_index": "ferric_index", "gentle_slope_frac": "custom:gentle_slope_frac",
    "permafrost_class": "custom:permafrost_class", "passable_2km": "custom:passable_2km",
    "passable_10km": "custom:passable_10km", "passable_50km": "custom:passable_50km",
}


def gee_band_names(ds) -> list[str]:
    names = [d for d in (ds.descriptions or ()) if d]
    if len(names) == ds.count and all(n in GEE_KIND for n in names):
        return names
    for bands in (GEE_BANDS, GEE_BANDS_V2, GEE_BANDS_V1):
        if ds.count == len(bands):
            return list(bands)
    raise SystemExit(f"{Path(ds.name).name}: expected {len(GEE_BANDS)} bands ({', '.join(GEE_BANDS)}), "
                     f"found {ds.count}. Was it exported by backend/gee/analog_features.js?")


def process_gee(region: Region, files: list[Path], replace: bool = True) -> list[Path]:
    """Earth Engine feature GeoTIFFs -> one cropped single-band layer per feature in the region.
    Re-importing replaces the region's previous Earth Engine layers (other layers are kept)."""
    layer_dir = region.dir / "layers"
    layer_dir.mkdir(parents=True, exist_ok=True)
    files = [Path(f) for f in files if intersects(raster_bounds(Path(f)), region.bbox)]
    if not files:
        raise SystemExit(f"None of the Earth Engine files overlap region {region.id}.")
    if replace:
        for kind in set(GEE_KIND.values()):
            kept = [p for p in region.layers.get(kind, []) if not Path(p).name.startswith("gee_")]
            if kept:
                region.layers[kind] = kept
            else:
                region.layers.pop(kind, None)
        for old in layer_dir.glob("gee_*.tif"):
            old.unlink()
    outs = []
    w, s, e, n = region.bbox
    for f in files:
        with rasterio.open(f) as ds:
            names = gee_band_names(ds)
            b = rasterio.warp.transform_bounds("EPSG:4326", ds.crs, w, s, e, n)
            win = rasterio.windows.from_bounds(*b, ds.transform).round_offsets().round_lengths()
            win = win.intersection(rasterio.windows.Window(0, 0, ds.width, ds.height))
            transform = ds.window_transform(win)
            profile = ds.profile.copy()
            profile.update(count=1, dtype="float32", nodata=np.nan, width=win.width, height=win.height,
                           transform=transform, compress="deflate", driver="GTiff")
            profile.pop("blockxsize", None), profile.pop("blockysize", None), profile.pop("tiled", None)
            for i, name in enumerate(names, start=1):
                a = ds.read(i, window=win).astype("float32")
                if ds.nodatavals and ds.nodatavals[i - 1] is not None and not np.isnan(ds.nodatavals[i - 1]):
                    a[a == ds.nodatavals[i - 1]] = np.nan
                out = layer_dir / f"gee_{f.stem}_{name}.tif"
                with rasterio.open(out, "w", **profile) as dst:
                    dst.write(a, 1)
                region.add_layer(GEE_KIND[name], out)
                outs.append(out)
        print(f"  Earth Engine: {f.name} -> {len(names)} feature layer(s) "
              f"({win.width} x {win.height} pixels inside the region)")
    return outs
