"""Import data you already have (or a URL) without knowing which command handles it.

    analog import data/inbox                      # a folder: everything recognised inside
    analog import EMIT_L2B_MIN_002_....nc         # a file: routed to every region it overlaps
    analog import srtm_tiles.zip --region my-site # a zip, limited to one region
    analog import https://.../some.tif --kind dem # a URL (Earthdata URLs use your login)
    analog import frt..._su..._mtr3.lbl           # CRISM summary -> a Mars target's minerals
    analog import moon_dem.tif --target moon_x --body moon   # DEM clip -> a target's terrain

Recognised formats (by file name):
    srtm   N24W070.hgt, N24W070.SRTMGL1.hgt.zip            -> region `dem`
    hls    HLS.L30|S30.<tile>.<date>.v2.0.<band>.tif       -> region albedo, ndvi, water, indices
    emit   EMIT_L2B_MIN_<ver>_<datetime>_*.nc               -> region mineral groups
    crism  <obs>_07_su<nnn>j_mtr3.img/.lbl (+ the .lbl)     -> Mars target mineral groups
    raster any other GeoTIFF / GDAL raster                  -> needs --kind (guessed from the name if
                                                               it contains dem, ndvi, albedo, water...)
"""

from __future__ import annotations

import fnmatch
import re
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import yaml

from . import crism, paths, process
from .rasterops import terrain_profile
from .region import KIND_RE, Region
from .targets import load_targets, write_user_target

RASTER_EXT = {".tif", ".tiff", ".img", ".vrt", ".jp2", ".nc", ".h5", ".hdf"}
GUESS_KIND = [  # (regex on lower-cased file name, kind)
    (r"(^|[_\-.])(dem|dtm|dsm|elev|elevation|srtm|copernicus|cop30|alos|lola|mola|hrsc)([_\-.]|$)", "dem"),
    (r"ndvi", "ndvi"),
    (r"albedo", "albedo"),
    (r"(^|[_\-.])water([_\-.]|$)", "water"),
    (r"temp[_\-]?range|diurnal", "temp_range"),
]
EARTHDATA_HOSTS = ("earthdata.nasa.gov", "earthdatacloud.nasa.gov", "lpdaac", "cmr.earthdata")
DOWNLOADS = paths.DATA / "downloads"
EXTRACTED = paths.DATA / "inbox" / ".extracted"


# ---- collecting files ---------------------------------------------------------------------

def _fetch(url: str, out: Path, session) -> Path:
    print(f"  downloading {url}")
    with session.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        tmp = out.with_suffix(out.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        tmp.replace(out)
    return out


def _download(url: str) -> Path:
    """Download a URL into data/downloads/ (skipped if already there). Earthdata URLs use your
    Earthdata login; other URLs (e.g. the PDS archive) are fetched anonymously."""
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    out = DOWNLOADS / (Path(urlparse(url).path).name or "download")
    if any(h in urlparse(url).netloc for h in EARTHDATA_HOSTS):
        from .fetch import login
        session = login().get_requests_https_session()
    else:
        import requests
        session = requests.Session()
    if out.exists() and out.stat().st_size > 0:
        print(f"  already downloaded: {out.name}")
    else:
        _fetch(url, out, session)
    # PDS products are an .IMG + .LBL pair: fetch the sibling too, so either URL is enough.
    if crism.is_crism_summary(out):
        stem_url = url[: -len(Path(urlparse(url).path).suffix)]
        for ext in (".lbl", ".img") if out.suffix.lower() == ".img" else (".img", ".lbl"):
            sib = out.with_suffix(ext if out.suffix.islower() else ext.upper())
            if sib.exists() or sib.suffix.lower() == out.suffix.lower():
                continue
            try:
                _fetch(stem_url + (ext if url[-4:].islower() else ext.upper()), sib, session)
                break
            except Exception as e:
                print(f"  could not fetch the paired {ext} file ({e}); download it manually")
    return out


def collect(sources: list[str]) -> list[Path]:
    """Expand files, folders (recursive), globs, zips and URLs into a list of local files."""
    out: list[Path] = []
    for s in sources:
        if re.match(r"^https?://", s):
            out.append(_download(s))
            continue
        p = Path(s)
        # Relative paths: try the current folder, then the project root (e.g. from a subfolder).
        if not p.is_absolute() and not p.exists() and (paths.ROOT / s).exists():
            p = paths.ROOT / s
        if any(ch in s for ch in "*?["):
            hits = [q for q in Path(".").glob(s) if q.is_file()]
            out += hits or [q for q in paths.ROOT.glob(s) if q.is_file()]
        elif p.is_dir():
            out += [q for q in p.rglob("*") if q.is_file() and ".extracted" not in q.parts]
            ex = p / ".extracted"
            if ex.is_dir():
                out += [q for q in ex.rglob("*") if q.is_file()]
        elif p.is_file():
            out.append(p)
        else:
            raise SystemExit(f"Not found: {s}")
    expanded: list[Path] = []
    for f in out:
        if f.suffix.lower() == ".zip" and not process.SRTM_NAME.match(f.name):
            dest = EXTRACTED / f.stem
            if not dest.exists():
                with zipfile.ZipFile(f) as z:
                    z.extractall(dest)
            expanded += [q for q in dest.rglob("*") if q.is_file()]
        else:
            expanded.append(f)
    # de-duplicate, keep order
    seen, uniq = set(), []
    for f in expanded:
        k = str(f.resolve()).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return uniq


# ---- detection -------------------------------------------------------------------------------

def detect(path: Path) -> str | None:
    name = path.name
    if process.SRTM_NAME.match(name):
        return "srtm"
    if process.HLS_NAME.match(name):
        return "hls"
    if process.GEE_NAME.match(name):
        return "gee"  # multi-band feature export from backend/gee/analog_features.js
    if name.upper().startswith("EMIT_"):
        # Only the L2B mineral product is used; uncertainty and other EMIT products are skipped.
        return "emit" if process.EMIT_NAME.match(name) and "UNCERT" not in name.upper() else None
    if crism.is_crism_summary(path):
        return "crism"
    if path.suffix.lower() in {".lbl", ".xml", ".aux", ".ovr", ".md", ".txt", ".json", ".geojson", ".csv"}:
        return None
    if path.suffix.lower() in RASTER_EXT:
        return "raster"
    return None


def guess_kind(path: Path) -> str | None:
    low = path.name.lower()
    for pattern, kind in GUESS_KIND:
        if re.search(pattern, low):
            return kind
    return None


def footprint(path: Path, kind: str):
    if kind == "srtm":
        return process.srtm_bounds(path.name) or process.raster_bounds(path)
    if kind == "emit":
        return process.emit_bounds(path)
    return process.raster_bounds(path)


# ---- planetary targets -------------------------------------------------------------------------

def _merge_target(target_id: str, body: str, new_features: dict, note: str, base: str | None = None,
                  extra: dict | None = None):
    targets = load_targets()
    existing = targets.get(target_id) or (targets.get(base) if base else None)
    t = {k: v for k, v in (existing or {}).items() if k not in ("id", "source_file")}
    t.setdefault("name", target_id.replace("_", " ").title())
    t["body"] = body
    t.setdefault("category", "imported")
    t.setdefault("description", note)
    feats = dict(t.get("features") or {})
    for k, spec in new_features.items():
        old = feats.get(k, {})
        merged = {**old, **spec}
        if "weight" in old:
            merged["weight"] = old["weight"]
        feats[k] = merged
    t["features"] = feats
    old_notes = str(t.get("notes") or "")
    if note not in old_notes:  # re-importing the same file shouldn't repeat its note
        t["notes"] = (old_notes + " | " if old_notes else "") + note
    if extra:
        t.update(extra)
    out = write_user_target(target_id, t)
    return out


def import_crism(files: list[Path], target: str | None, base: str | None):
    for f in files:
        shares, info = crism.group_shares(f)
        tid = target or f"mars_crism_{info['observation'].lower()}"
        feats = {f"min_{g}": {"value": round(v, 3), "sigma": round(max(0.1, v * 0.5), 3), "weight": 1.0}
                 for g, v in shares.items()}
        lat = lon = None
        if isinstance(info.get("min_lat"), float) and isinstance(info.get("max_lat"), float):
            lat = round((info["min_lat"] + info["max_lat"]) / 2, 3)
        if isinstance(info.get("west_lon"), float) and isinstance(info.get("east_lon"), float):
            lon = (info["west_lon"] + info["east_lon"]) / 2
            lon = round(lon - 360 if lon > 180 else lon, 3)
        note = (f"Minerals from CRISM {info['observation']} ({info['valid_pixels']:,} valid pixels; "
                f"share of pixels above config/crism_groups.yaml thresholds)")
        extra = {"lat": lat, "lon": lon} if lat is not None and lon is not None else None
        if target is None and base is None:
            extra = {**(extra or {}), "category": "CRISM observation"}
        out = _merge_target(tid, "mars", feats, note, base, extra)
        print(f"  CRISM {info['observation']}: " + ", ".join(f"{g} {v:.1%}" for g, v in shares.items()))
        print(f"  -> target {tid} ({out.relative_to(paths.ROOT)})")


def import_planet_raster(files: list[Path], target: str, body: str, kind: str | None,
                         base: str | None, pixel_m: float | None):
    for f in files:
        k = kind or guess_kind(f)
        if k != "dem":
            raise SystemExit(f"{f.name}: only DEMs (terrain) can be imported into a target so far; "
                             f"pass --kind dem if it is one. CRISM summaries are detected automatically.")
        prof = terrain_profile(f, body, 1000.0, pixel_m)
        for w in prof["warnings"]:
            print(f"  warning: {w}")
        note = f"Terrain from {f.name} at {prof['pixel_m']} m/px over {prof['blocks']} blocks of ~1 km"
        out = _merge_target(target, body, prof["features"], note, base)
        print("  " + ", ".join(f"{k} {v['value']}" for k, v in prof["features"].items()))
        print(f"  -> target {target} ({out.relative_to(paths.ROOT)})")


# ---- main entry point ------------------------------------------------------------------------------

def import_sources(sources: list[str], region_ids: list[str] | None = None, kind: str | None = None,
                   target: str | None = None, body: str | None = None, base: str | None = None,
                   scenes_per_tile: int = 2, pixel_m: float | None = None, dry_run: bool = False,
                   append: bool = False) -> list[str]:
    """Import files; returns the ids of the regions that changed."""
    files = collect(sources)
    if kind and not KIND_RE.match(kind):
        raise SystemExit(f"Unknown --kind {kind!r}")
    groups: dict[str, list[Path]] = {}
    for f in files:
        k = detect(f)
        if k:
            groups.setdefault(k, []).append(f)
    if not groups:
        raise SystemExit("Nothing recognised. Supported: SRTM .hgt(.zip), HLS band .tif, EMIT L2B MIN .nc, "
                         "CRISM summary .img/.lbl, Earth Engine analogfeat_*.tif, or any GeoTIFF with --kind.")

    # Moon / Mars data -> targets
    if target or body in ("moon", "mars"):
        rasters = groups.pop("raster", [])
        if rasters:
            if not target or not body:
                raise SystemExit("For a Moon/Mars raster pass --target ID and --body moon|mars.")
            if not dry_run:
                import_planet_raster(rasters, target, body, kind, base, pixel_m)
    if "crism" in groups:
        files_c = [f for f in groups.pop("crism") if f.suffix.lower() == ".lbl"] or groups.get("crism", [])
        # one entry per observation (the .img and .lbl are a pair)
        by_obs = {}
        for f in files_c:
            by_obs[crism.SU_NAME.match(f.name)["obs"].lower()] = f
        print(f"CRISM summary products: {len(by_obs)}")
        if not dry_run:
            import_crism(list(by_obs.values()), target, base)

    # Earth data -> regions
    regions = [Region.load(r) for r in region_ids] if region_ids else Region.all()
    changed: list[str] = []
    plan = []
    for k, fs in groups.items():
        for f in fs:
            fp = footprint(f, k)
            hits = [r.id for r in regions if process.intersects(fp, r.bbox)]
            plan.append((k, f, fp, hits))
    if plan:
        print(f"{'type':6} {'file':60} regions")
        for k, f, _fp, hits in plan:
            print(f"{k:6} {f.name[:60]:60} {', '.join(hits) or '(no region overlaps)'}")
    if dry_run:
        return []

    for r in regions:
        mine = {k: [f for kk, f, fp, hits in plan if kk == k and r.id in hits] for k in groups}
        mine = {k: v for k, v in mine.items() if v}
        if not mine:
            continue
        print(f"\n== {r.id}")
        if "srtm" in mine:
            process.process_srtm(r, mine["srtm"])
        if "hls" in mine:
            process.process_hls(r, mine["hls"], scenes_per_tile, replace=not append)
        if "emit" in mine:
            process.process_emit(r, mine["emit"], replace=not append)
        if "gee" in mine:
            process.process_gee(r, mine["gee"], replace=not append)
        for f in mine.get("raster", []):
            k = kind or guess_kind(f)
            if not k:
                print(f"  skip {f.name}: can't tell what it measures; rerun with --kind (e.g. dem, ndvi, "
                      f"custom:my_feature)")
                continue
            r.add_layer(k, f)
            print(f"  {k}: {f.name}")
        changed.append(r.id)
    if plan and not changed:
        print("No region overlaps these files. Create one first, e.g.:")
        k, f, fp, _ = plan[0]
        if fp:
            w, s, e, n = fp
            print(f"  analog region create my-site --bbox {w:.2f} {s:.2f} {min(e, w + 1):.2f} {min(n, s + 1):.2f}")
    return changed


# ---- sync: inbox first, then download what's still missing ------------------------------------------

def load_sources() -> dict:
    p = paths.CONFIG / "sources.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}


def _opts(cfg: dict, region_id: str, source: str) -> dict:
    base = dict((cfg.get("sources") or {}).get(source) or {})
    for pattern, over in (cfg.get("regions") or {}).items():
        if fnmatch.fnmatch(region_id, pattern):
            base.update((over or {}).get(source) or {})
    return base


def has_source(region: Region, source: str) -> bool:
    if source == "srtm":
        return "dem" in region.layers
    if source == "hls":
        return "water" in region.layers
    if source == "emit":
        return any(k.startswith("mineral:") for k in region.layers) or (region.dir / ".no_emit").exists()
    return False


def sync(region_ids: list[str], force: bool = False, offline: bool = False) -> list[str]:
    """For each region: import matching files from the inbox folder, then download from NASA
    whatever is still missing. Already-present sources are skipped unless force=True."""
    from . import fetch

    cfg = load_sources()
    inbox = paths.ROOT / (cfg.get("inbox") or "data/inbox")
    changed = []
    for rid in region_ids:
        r = Region.load(rid)
        print(f"\n=== {rid}")
        inbox_files = collect([str(inbox)]) if inbox.exists() else []
        local = {"srtm": [], "hls": [], "emit": []}
        for f in inbox_files:
            k = detect(f)
            if k in local and process.intersects(footprint(f, k), r.bbox):
                local[k].append(f)
        for source in ("srtm", "hls", "emit"):
            opts = _opts(cfg, rid, source)
            if opts.get("enabled") is False:
                continue
            if has_source(r, source) and not force:
                print(f"  {source}: already present (use --force to redo)")
                continue
            try:
                if local[source]:
                    print(f"  {source}: {len(local[source])} file(s) from {inbox.relative_to(paths.ROOT)}")
                    if source == "srtm":
                        process.process_srtm(r, local[source])
                    elif source == "hls":
                        process.process_hls(r, local[source], int(opts.get("scenes_per_tile", 2)))
                    else:
                        process.process_emit(r, local[source])
                elif offline:
                    print(f"  {source}: nothing in the inbox (offline: not downloading)")
                    continue
                elif source == "srtm":
                    fetch.fetch_srtm(r)
                elif source == "hls":
                    fetch.fetch_hls(r, str(opts.get("start", "2025-01-01")), str(opts.get("end", "2025-10-31")),
                                    int(opts.get("max_cloud", 10)), int(opts.get("scenes_per_tile", 2)))
                else:
                    try:
                        fetch.fetch_emit(r, opts.get("start"), opts.get("end"), int(opts.get("max_granules", 3)))
                    except SystemExit as e:
                        if "No EMIT" in str(e):
                            (r.dir / ".no_emit").write_text("EMIT has no granules here\n")
                        raise
                changed.append(rid)
            except SystemExit as e:
                print(f"  {source}: {e}")
            r = Region.load(rid)
    return sorted(set(changed))
