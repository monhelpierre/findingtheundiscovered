"""Worldwide screen: cut Earth Engine continent exports into tile regions automatically.

`analog world import data/inbox/gee` takes the whole-land files written by backend/gee/analog_features.js
(SITES = [], analogfeat_earth_<continent>.tif, 0.05 deg cells) and, instead of you drawing regions:

1. splits each file's area into TILE_DEG x TILE_DEG tiles on the file's own grid (so cells line up),
2. keeps the tiles with enough land (MIN_LAND of the cells have elevation and are not mostly water),
3. creates each one as a region with scope "world" (id world-n35w010 = tile's south-west corner),
4. imports the Earth Engine bands into it (process.process_gee) and computes its features.

Tiles shared by two continent files (their boxes overlap) are one region fed by both files.
`analog export` then scores them like any region, but lists their candidates separately
(index.json "candidates_world") and writes cell files only for tiles holding a top candidate, so the
web app stays small; its "Worldwide" checkbox switches to them.
"""

from __future__ import annotations

import logging
import math
import time
from pathlib import Path

import numpy as np
import rasterio
import rasterio.windows

from . import paths, process
from .importer import collect, detect
from .region import Region

log = logging.getLogger(__name__)

TILE_DEG = 5.0
MIN_LAND = 0.05
SCOPE = "world"


def tile_id(w: float, s: float) -> str:
    ns = f"n{round(s):02d}" if s >= 0 else f"s{round(-s):02d}"
    ew = f"e{round(w):03d}" if w >= 0 else f"w{round(-w):03d}"
    return f"world-{ns}{ew}"


def _fmt_lat(v: float) -> str:
    return f"{abs(v):g}°{'N' if v >= 0 else 'S'}"


def _fmt_lon(v: float) -> str:
    return f"{abs(v):g}°{'E' if v >= 0 else 'W'}"


def tiles_for(path: Path, tile_deg: float = TILE_DEG, min_land: float = MIN_LAND) -> list[tuple[list[float], float]]:
    """[(bbox, land share)] of the tiles of one Earth Engine file that have enough land.
    Tiles start at the file's north-west corner, so tile edges fall on the file's cell grid."""
    with rasterio.open(path) as ds:
        names = process.gee_band_names(ds)
        elev = ds.read(names.index("elev_mean") + 1).astype("float32")
        water = ds.read(names.index("water_fraction") + 1).astype("float32")
        res = ds.transform.a
        west, north = ds.transform.c, ds.transform.f
        land = np.isfinite(elev) & ~(np.nan_to_num(water, nan=0) > 0.5)
        step = int(round(tile_deg / res))
        out = []
        for r0 in range(0, ds.height, step):
            for c0 in range(0, ds.width, step):
                block = land[r0:r0 + step, c0:c0 + step]
                share = float(block.mean()) if block.size else 0.0
                if share < min_land:
                    continue
                w = round(west + c0 * res, 6)
                n = round(north - r0 * res, 6)
                e = round(min(w + tile_deg, west + ds.width * res), 6)
                s = round(max(n - tile_deg, north - ds.height * res), 6)
                out.append(([w, s, e, n], share))
    return out


def import_world(sources: list[str], tile_deg: float = TILE_DEG, min_land: float = MIN_LAND,
                 compute: bool = True) -> list[Region]:
    from . import features as feat_mod

    files = [f for f in collect(sources) if detect(f) == "gee"]
    if not files:
        raise SystemExit("No Earth Engine feature files (analogfeat_*.tif) found in: " + " ".join(sources))
    res = {round(rasterio.open(f).transform.a, 6) for f in files}
    if len(res) != 1:
        raise SystemExit(f"Earth Engine files have different cell sizes {sorted(res)}; import them separately.")
    cell = res.pop()
    log.info("World screen: %d file(s), %.3g deg cells, %g deg tiles, land >= %.0f%%",
             len(files), cell, tile_deg, 100 * min_land)

    wanted: dict[str, list[float]] = {}
    for f in files:
        tiles = tiles_for(f, tile_deg, min_land)
        log.info("  %s: %d tile(s) with land", f.name, len(tiles))
        for bbox, _ in tiles:
            wanted.setdefault(tile_id(bbox[0], bbox[1]), bbox)

    regions = []
    t0 = time.perf_counter()
    for k, (rid, bbox) in enumerate(sorted(wanted.items()), 1):
        w, s, e, n = bbox
        title = f"World {_fmt_lat(s)}–{_fmt_lat(n)}, {_fmt_lon(w)}–{_fmt_lon(e)}"
        r = Region.load(rid) if (paths.REGIONS / rid / "region.json").exists() else None
        if r is None:
            r = Region(id=rid, bbox=bbox, cell_deg=cell, title=title, scope=SCOPE)
            r.save()
        elif r.scope != SCOPE:
            log.warning("  %s exists and is not a world tile; skipped", rid)
            continue
        mine = [f for f in files if process.intersects(process.raster_bounds(f), r.bbox)]
        with _quiet():
            process.process_gee(r, mine)
        r.save()
        if compute:
            with _quiet():
                feat_mod.compute(r)
        regions.append(r)
        if k % 10 == 0 or k == len(wanted):
            log.info("  [%d/%d] tiles imported%s, %.0f s", k, len(wanted), " and computed" if compute else "",
                     time.perf_counter() - t0)
    return regions


class _quiet:
    """Per-tile progress lines go to the log file only (hundreds of tiles would flood the screen)."""

    def __enter__(self):
        import contextlib
        import io
        self._buf = io.StringIO()
        self._cm = contextlib.redirect_stdout(self._buf)
        self._cm.__enter__()
        self._levels = {}
        for name in ("analog.features", "analog.process"):
            lg = logging.getLogger(name)
            self._levels[name] = lg.level
            lg.setLevel(logging.WARNING)
        return self

    def __exit__(self, *exc):
        self._cm.__exit__(*exc)
        for name, lv in self._levels.items():
            logging.getLogger(name).setLevel(lv)
        for line in self._buf.getvalue().splitlines():
            log.debug("    %s", line)
        return False


def summary(regions: list[Region]) -> str:
    cells = sum(math.prod(r.shape) for r in regions)
    return f"{len(regions)} world tile(s), {cells:,} cells"
