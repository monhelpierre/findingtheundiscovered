"""Search and download NASA Earth data for a region with earthaccess (free Earthdata login),
then hand the files to process.py, the same code `analog import` uses for local files."""

from __future__ import annotations

from pathlib import Path

from . import process
from .region import Region


def login():
    import earthaccess

    auth = earthaccess.login()  # uses ~/.netrc, env vars, or prompts
    if not getattr(auth, "authenticated", False):
        raise SystemExit("Earthdata login failed. Register at https://urs.earthdata.nasa.gov/users/new")
    return earthaccess


def _raw(region: Region, name: str) -> Path:
    d = region.dir / "raw" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _attr(g, name: str, default: float) -> float:
    for a in g["umm"].get("AdditionalAttributes", []):
        if a.get("Name") == name:
            try:
                return float(a["Values"][0])
            except (KeyError, ValueError, IndexError):
                pass
    return default


# ---- SRTM ----------------------------------------------------------------------

def fetch_srtm(region: Region) -> list[Path]:
    ea = login()
    w, s, e, n = region.bbox
    if s < -56 or n > 60:
        print("  warning: SRTM covers 60N-56S only; cells outside will have no terrain.")
    results = ea.search_data(short_name="SRTMGL1", version="003", bounding_box=(w, s, e, n))
    if not results:
        raise SystemExit("No SRTM tiles found for this bbox.")
    print(f"  {len(results)} SRTM tile(s)")
    files = ea.download(results, local_path=str(_raw(region, "srtm")))
    return process.process_srtm(region, [Path(f) for f in files])


# ---- HLS (albedo, NDVI, water, mineral indices) ----------------------------------

def fetch_hls(region: Region, start: str, end: str, max_cloud: int = 10,
              scenes_per_tile: int = 2) -> list[Path]:
    """Per MGRS tile, download the scenes that cover most of the tile with the fewest clouds.

    A Landsat pass often clips the edge of a tile, so one scene can leave a gap; with
    scenes_per_tile > 1 the next-best scene fills it (first valid value wins when gridding).
    """
    ea = login()
    results = ea.search_data(short_name="HLSL30", version="2.0", bounding_box=tuple(region.bbox),
                             temporal=(start, end), cloud_cover=(0, max_cloud), count=500)
    if not results:
        raise SystemExit("No HLS L30 scenes found; widen the dates or raise --max-cloud.")
    by_tile: dict[str, list] = {}
    for g in results:
        by_tile.setdefault(g["umm"]["GranuleUR"].split(".")[2], []).append(g)
    print(f"  {len(results)} scenes over {len(by_tile)} MGRS tile(s); "
          f"using up to {scenes_per_tile} per tile (most coverage, fewest clouds)")
    raw = _raw(region, "hls")
    files, ranking = [], {}
    for _tile, gs in sorted(by_tile.items()):
        gs.sort(key=lambda g: (-_attr(g, "SPATIAL_COVERAGE", 100), _attr(g, "CLOUD_COVERAGE", 100)))
        for rank, g in enumerate(gs[:scenes_per_tile]):
            ur = g["umm"]["GranuleUR"]
            bands = process.hls_band_names(ur.split(".")[1])
            links = [u for u in g.data_links() if any(u.endswith(f".{b}.tif") for b in bands)]
            try:
                files += [Path(f) for f in ea.download(links, local_path=str(raw))]
            except Exception as e:  # a dropped connection shouldn't abort the whole region
                print(f"  skip {ur}: download failed ({type(e).__name__}: {e}); rerun to retry")
                continue
            ranking[ur] = 1.0 - rank / 10  # keep the metadata-based order
    return process.process_hls(region, files, scenes_per_tile, ranking=ranking)


# ---- EMIT minerals ------------------------------------------------------------------

def fetch_emit(region: Region, start: str | None, end: str | None, max_granules: int = 5) -> list[Path]:
    ea = login()
    kw = {"temporal": (start, end)} if start and end else {}
    results = ea.search_data(short_name="EMITL2BMIN", bounding_box=tuple(region.bbox),
                             count=max_granules * 4, **kw)
    if not results:
        raise SystemExit("No EMIT L2B mineral granules here (EMIT only maps arid regions, ~52N-52S).")
    # The same acquisition can exist in several collection versions (001, 002): keep the newest.
    newest: dict[str, tuple[str, object]] = {}
    for g in results:
        parts = g["umm"]["GranuleUR"].split("_")  # EMIT_L2B_MIN_<ver>_<timestamp>[_...]
        key, ver = (parts[4], parts[3]) if len(parts) > 4 else (g["umm"]["GranuleUR"], "")
        if key not in newest or ver > newest[key][0]:
            newest[key] = (ver, g)
    results = [g for _, g in sorted(newest.values(), key=lambda vg: vg[0], reverse=True)][:max_granules]
    print(f"  using {len(results)} EMIT granule(s)")
    raw = _raw(region, "emit")
    files = []
    for g in results:
        links = [u for u in g.data_links() if u.endswith(".nc") and "_MIN_" in u and "UNCERT" not in u]
        files += [Path(f) for f in ea.download(links, local_path=str(raw))]
    return process.process_emit(region, files)
