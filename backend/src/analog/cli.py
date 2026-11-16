"""analog: command-line pipeline.

  analog demo                                   synthetic region -> features -> scores -> export
  analog region create ID --bbox W S E N        define a study region
  analog fetch srtm|hls|emit ID                 download NASA data into the region
  analog import FILE|FOLDER|ZIP|URL ...         import data you already have (auto-detected)
  analog sync ID|--all                          inbox files first, then download what's missing
  analog ingest ID FILE --kind KIND             add one GeoTIFF to one region
  analog features ID|--all                      per-cell feature table
  analog target list | from-dem                 Moon/Mars target profiles
  analog train --labels FILE --regions A B      PU classifier on known analog sites
  analog earth-ref build | show                 Earth land percentiles for percentile target features
  analog world import FOLDER|FILES              Earth Engine continent files -> 5 deg world tiles (auto regions)
  analog world list                             the worldwide screen's tiles
  analog export                                 score all regions + write data for the web UI
  analog run ID                                 features + export in one go
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from . import log, paths
from .region import KIND_HELP, Region


def _regions(args) -> list[Region]:
    if getattr(args, "all", False):
        return Region.all()
    if not args.ids:
        raise SystemExit("Give region id(s) or --all")
    return [Region.load(i) for i in args.ids]


def cmd_region(args):
    if args.action == "create":
        r = Region.create(args.id, args.bbox, args.cell, args.title or "")
        h, w = r.shape
        print(f"Created region {r.id}: {h}x{w} = {h * w:,} cells of {r.cell_deg} deg at {r.dir}")
    elif args.action == "list":
        regions = Region.all()
        if not regions:
            print("No regions yet. Try: analog demo   or   analog region create ID --bbox W S E N")
        for r in regions:
            h, w = r.shape
            feats = "yes" if (r.dir / "features.parquet").exists() else "no"
            print(f"{r.id:24} bbox={r.bbox} cells={h * w:,} features={feats} layers={sorted(r.layers)}")
    elif args.action == "show":
        r = Region.load(args.id)
        print(f"{r.id}: {r.title}\n  bbox {r.bbox}, cell {r.cell_deg} deg, grid {r.shape}")
        for k, v in r.layers.items():
            for p in v:
                print(f"  {k:24} {p}")


def cmd_fetch(args):
    from . import fetch
    r = Region.load(args.id)
    if args.source == "srtm":
        print(f"SRTM -> {fetch.fetch_srtm(r)}")
    elif args.source == "hls":
        outs = fetch.fetch_hls(r, args.start, args.end, args.max_cloud, args.scenes_per_tile)
        print(f"HLS -> {len(outs)} layer file(s)")
    elif args.source == "emit":
        outs = fetch.fetch_emit(r, args.start, args.end, args.max_granules)
        print(f"EMIT -> {len(outs)} mineral-group raster(s)")
    elif args.source == "meteomatics":
        from . import meteomatics
        outs = meteomatics.fetch_region(r, args.year, args.res, offline=args.offline)
        print(f"Meteomatics -> {len(outs)} climate feature layer(s)")
    print(f"Next: analog features {r.id}")


def cmd_ingest(args):
    r = Region.load(args.id)
    for f in args.files:
        dest = r.add_layer(args.kind, f, copy=not args.link)
        print(f"Added {args.kind}: {dest}")
    print(f"Next: analog features {r.id}")


def cmd_import(args):
    from .importer import import_sources
    changed = import_sources(args.sources, args.region or None, args.kind, args.target, args.body,
                             args.base, args.scenes_per_tile, args.pixel_m, args.dry_run, args.append)
    if changed and args.run:
        cmd_run(argparse.Namespace(ids=changed, all=False))
    elif changed:
        print()
        print(f"Next: analog run {' '.join(changed)}")
    elif not args.dry_run:
        print("Next: analog export")


def cmd_sync(args):
    from .importer import sync
    ids = [r.id for r in Region.all() if r.id != "demo-synthetic"] if args.all else args.ids
    if not ids:
        raise SystemExit("Give region id(s) or --all")
    changed = sync(ids, args.force, args.offline)
    if args.run and ids:
        cmd_run(argparse.Namespace(ids=ids, all=False))
    else:
        print()
        print(f"Changed: {', '.join(changed) or 'nothing'}. Next: analog run {' '.join(ids)}")


def cmd_features(args):
    from . import features
    for r in _regions(args):
        df = features.compute(r)
        cols = [c for c in df.columns if c not in features.META_COLS]
        print(f"{r.id}: {len(df):,} cells with data")
        print(df[cols].describe().T[["count", "mean", "min", "max"]].round(3).to_string())


def cmd_target(args):
    from .targets import load_targets, write_user_target
    if args.action == "list":
        for tid, t in load_targets().items():
            feats = ", ".join(t["features"])
            print(f"{tid:28} [{t['body']}] {t.get('name', '')}\n{'':28}   {feats}  ({t['source_file']})")
    elif args.action == "from-dem":
        from .rasterops import terrain_profile
        prof = terrain_profile(args.dem, args.body, args.cell_m, args.pixel_m)
        for w in prof["warnings"]:
            print(f"warning: {w}")
        existing = load_targets().get(args.base) if args.base else None
        feats = dict(existing["features"]) if existing else {}
        feats.update(prof["features"])
        t = {"name": args.name or args.id, "body": args.body,
             "category": args.category or "user-defined",
             "description": args.description or f"Terrain profile derived from {Path(args.dem).name}",
             "features": feats,
             "notes": f"Terrain from {Path(args.dem).name} at {prof['pixel_m']} m/px over "
                      f"{prof['blocks']} blocks of ~{args.cell_m:.0f} m"
                      + (f"; other features copied from {args.base}" if existing else "")}
        if args.lat is not None:
            t["lat"], t["lon"] = args.lat, args.lon
        out = write_user_target(args.id, t)
        print(f"Wrote target {args.id} -> {out}")
        for k, v in prof["features"].items():
            print(f"  {k:12} value={v['value']:<8} sigma={v['sigma']}")
        print("Next: analog export")


def cmd_train(args):
    from .classifier import train
    ids = [r.id for r in Region.all()] if args.all else args.regions
    if not ids:
        raise SystemExit("Give --regions A B ... or --all")
    import pandas as pd
    exclude = set(args.exclude or [])
    if args.skip_meteomatics:
        from .meteomatics import FEATURES as MM_FEATURES
        exclude |= set(MM_FEATURES)
    b = train(Path(args.labels), ids, args.bags, args.seed, exclude=exclude)
    if b["validation"]:
        print("\nLeave-one-site-out within each type (AUC: 0.5 = chance, 1 = perfect; "
              "auc_bare = against bare ground only, the harder test):")
        print(pd.DataFrame(b["validation"]).fillna("").to_string(index=False))
    print("Next: analog export")


def cmd_earthref(args):
    import numpy as np

    from . import earthref

    if args.action == "show":
        ref = earthref.load_reference()
        if not ref["features"]:
            raise SystemExit(f"No Earth reference yet ({earthref.reference_path()}). Run: analog earth-ref build")
        print(f"{earthref.reference_path()}: {ref.get('source')} (built {ref.get('built')})")
        print(f"{'feature':24} {'n':>6} {'p5':>10} {'p25':>10} {'p50':>10} {'p75':>10} {'p95':>10}")
        for name, f in ref["features"].items():
            q = f["quantiles"]
            print(f"{name:24} {f['n']:>6} " + " ".join(f"{q[i]:>10.4g}" for i in (50, 250, 500, 750, 950)))
        return
    if args.source == "meteomatics":
        from . import meteomatics

        samples = meteomatics.fetch_global(year=args.year, res=args.res, offline=args.offline)
        source = f"Meteomatics {args.year}, global {args.res} deg grid, land only, 4 days a month"
    else:
        regions = [Region.load(i) for i in args.regions] if args.regions else Region.all()
        samples = earthref.from_regions(regions)
        source = "regions: " + ", ".join(r.id for r in regions)
        if args.only:
            samples = {k: v for k, v in samples.items() if k in set(args.only)}
    if args.merge:  # keep the other features already in the reference
        old = earthref.load_reference()["features"]
        keep = {k: (np.asarray(v["quantiles"]), np.ones(len(v["quantiles"])))
                for k, v in old.items() if k not in samples}
        samples = {**keep, **samples}
        source += " (merged with the previous reference)"
    out = earthref.write_reference(samples, source)
    print(f"Wrote {out}. Percentile target features now use it; next: analog export")


def cmd_world(args):
    from . import world

    if args.action == "list":
        tiles = [r for r in Region.all() if r.scope == world.SCOPE]
        for r in tiles:
            done = "features" if (r.dir / "features.parquet").exists() else "no features yet"
            print(f"{r.id:20} {r.title:40} {done}")
        print(world.summary(tiles))
        return
    if not args.sources:
        raise SystemExit("Give the Earth Engine files or folder, e.g. analog world import data/inbox/gee")
    regions = world.import_world(args.sources, tile_deg=args.tile, min_land=args.min_land,
                                 compute=not args.no_features)
    print(f"Imported {world.summary(regions)}.")
    if args.run:
        cmd_export(argparse.Namespace(no_rescore=False))
    else:
        print("Next: analog export (world candidates appear under the app's Worldwide checkbox)")


def cmd_export(args):
    from .export import export
    export(rescore=not args.no_rescore)


def cmd_sensitivity(args):
    from .sensitivity import run
    run(top=args.top)


def cmd_moonbase(args):
    from .export import publish_to_web
    from .moonbase import import_sites
    import_sites(Path(args.path))
    publish_to_web()


def cmd_run(args):
    cmd_features(args)
    cmd_export(argparse.Namespace(no_rescore=False))


def cmd_demo(args):
    from . import demo
    r = demo.build()
    print(f"Built synthetic region {r.id}")
    cmd_features(argparse.Namespace(ids=[r.id], all=False))
    if args.train:
        print("note: the classifier is trained on SYNTHETIC labels and will be applied to every region "
              "until you retrain with real labels (analog train ...) or delete data/models/classifier.joblib")
        cmd_train(argparse.Namespace(labels=str(paths.LABELS / "demo_labels.geojson"), regions=[r.id],
                                     all=False, bags=5, seed=0, exclude=None, skip_meteomatics=False))
    cmd_export(argparse.Namespace(no_rescore=False))
    print("Now start the UI:  cd web && npm run dev")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="analog", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("region", help="create / list / show regions")
    ss = s.add_subparsers(dest="action", required=True)
    c = ss.add_parser("create")
    c.add_argument("id")
    c.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("W", "S", "E", "N"))
    c.add_argument("--cell", type=float, default=0.01, help="cell size in degrees (default 0.01 ~ 1 km)")
    c.add_argument("--title")
    ss.add_parser("list")
    sh = ss.add_parser("show")
    sh.add_argument("id")
    s.set_defaults(func=cmd_region)

    s = sub.add_parser("fetch", help="download NASA data (Earthdata login) or Meteomatics climate data")
    s.add_argument("source", choices=["srtm", "hls", "emit", "meteomatics"])
    s.add_argument("id")
    s.add_argument("--start", default="2024-03-01", help="hls/emit start date")
    s.add_argument("--end", default="2025-10-31", help="hls/emit end date")
    s.add_argument("--max-cloud", type=int, default=10)
    s.add_argument("--scenes-per-tile", type=int, default=2,
                   help="hls: scenes per MGRS tile; extra scenes fill gaps in the best one")
    s.add_argument("--max-granules", type=int, default=5)
    s.add_argument("--year", type=int, default=2024, help="meteomatics: year of daily data to summarise")
    s.add_argument("--res", type=float, default=0.05, help="meteomatics: grid spacing in degrees")
    s.add_argument("--offline", action="store_true", help="meteomatics: only use cached responses")
    s.set_defaults(func=cmd_fetch)

    s = sub.add_parser("import", help="import files/folders/zips/URLs; format detected automatically")
    s.add_argument("sources", nargs="+", help="files, folders, globs, .zip or http(s) URLs")
    s.add_argument("--region", action="append", help="limit to this region (repeatable); default: all overlapping")
    s.add_argument("--kind", help="what a plain raster measures: " + KIND_HELP)
    s.add_argument("--target", help="Moon/Mars data: target id to create or update")
    s.add_argument("--body", choices=["moon", "mars", "earth"], help="body of a target raster")
    s.add_argument("--base", help="copy the other features of a new target from this target")
    s.add_argument("--pixel-m", type=float, help="pixel size override for planetary DEMs")
    s.add_argument("--scenes-per-tile", type=int, default=2)
    s.add_argument("--append", action="store_true", help="add to existing HLS/EMIT layers instead of replacing")
    s.add_argument("--dry-run", action="store_true", help="only show what would be imported where")
    s.add_argument("--run", action="store_true", help="then compute features and export")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("sync", help="per region: import inbox files, then download what's missing")
    s.add_argument("ids", nargs="*")
    s.add_argument("--all", action="store_true")
    s.add_argument("--force", action="store_true", help="redo sources the region already has")
    s.add_argument("--offline", action="store_true", help="only use local files, never download")
    s.add_argument("--run", action="store_true", help="then compute features and export")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("ingest", help="add your own GeoTIFF layer(s)")
    s.add_argument("id")
    s.add_argument("files", nargs="+")
    s.add_argument("--kind", required=True, help=KIND_HELP)
    s.add_argument("--link", action="store_true", help="reference the file instead of copying it")
    s.set_defaults(func=cmd_ingest)

    for name, fn, hlp in (("features", cmd_features, "compute per-cell features"),
                          ("run", cmd_run, "features + score + export")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("ids", nargs="*")
        s.add_argument("--all", action="store_true")
        s.set_defaults(func=fn)

    s = sub.add_parser("target", help="Moon/Mars target profiles")
    ss = s.add_subparsers(dest="action", required=True)
    ss.add_parser("list")
    t = ss.add_parser("from-dem", help="derive a terrain profile from a Moon/Mars DEM clip (GeoTIFF)")
    t.add_argument("id")
    t.add_argument("--dem", required=True)
    t.add_argument("--body", choices=["moon", "mars", "earth"], required=True)
    t.add_argument("--name")
    t.add_argument("--category")
    t.add_argument("--description")
    t.add_argument("--base", help="copy non-terrain features (minerals, albedo...) from this target")
    t.add_argument("--cell-m", type=float, default=1000.0)
    t.add_argument("--pixel-m", type=float, help="override pixel size if the CRS isn't recognised")
    t.add_argument("--lat", type=float)
    t.add_argument("--lon", type=float)
    s.set_defaults(func=cmd_target)

    s = sub.add_parser("train", help="train the PU classifier on known analog sites")
    s.add_argument("--labels", default=str(paths.LABELS / "known_analogs.geojson"))
    s.add_argument("--regions", nargs="*")
    s.add_argument("--all", action="store_true")
    s.add_argument("--bags", type=int, default=10)
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--skip-meteomatics", action="store_true",
                   help="train without the Meteomatics climate features (tmin_k, wind_ms, dust_aod, ...)")
    s.add_argument("--exclude", nargs="+", metavar="FEATURE", help="leave these features out of training")
    s.set_defaults(func=cmd_train)

    s = sub.add_parser("earth-ref", help="Earth land percentiles, for target features given as `percentile:`")
    s.add_argument("action", choices=["build", "show"])
    s.add_argument("--source", choices=["meteomatics", "regions"], default="meteomatics",
                   help="meteomatics: global coarse grid of the 9 climate features (default); "
                        "regions: your regions' feature tables (e.g. the Earth Engine whole-land import)")
    s.add_argument("--year", type=int, default=2024)
    s.add_argument("--res", type=float, default=5.0, help="Meteomatics grid spacing in degrees (default 5)")
    s.add_argument("--offline", action="store_true", help="rebuild from saved Meteomatics responses")
    s.add_argument("--regions", nargs="+", metavar="ID", help="--source regions: which regions (default all)")
    s.add_argument("--only", nargs="+", metavar="FEATURE", help="--source regions: only these features")
    s.add_argument("--merge", action="store_true", help="keep features of the existing reference not rebuilt now")
    s.set_defaults(func=cmd_earthref)

    s = sub.add_parser("world", help="worldwide screen: Earth Engine continent files -> automatic tile regions")
    s.add_argument("action", choices=["import", "list"])
    s.add_argument("sources", nargs="*", help="analogfeat_earth_*.tif files or folders (import)")
    s.add_argument("--tile", type=float, default=5.0, help="tile size in degrees (default 5)")
    s.add_argument("--min-land", type=float, default=0.05, help="skip tiles with less land than this share")
    s.add_argument("--no-features", action="store_true", help="only create and fill the tiles")
    s.add_argument("--run", action="store_true", help="then export")
    s.set_defaults(func=cmd_world)

    s = sub.add_parser("export", help="score every region and write data/export for the UI")
    s.add_argument("--no-rescore", action="store_true")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("sensitivity",
                       help="weight sensitivity: top cells kept when each criterion's weights move by 20%%")
    s.add_argument("--top", type=int, default=100, help="size of the top list to compare (default 100)")
    s.set_defaults(func=cmd_sensitivity)

    s = sub.add_parser("moonbase", help="add the sister project's Artemis III Moon-base sites to the web app")
    s.add_argument("path", help="its sites.parquet or sites_moon.csv (or the repository folder)")
    s.set_defaults(func=cmd_moonbase)

    s = sub.add_parser("demo", help="build the synthetic demo region and export it")
    s.add_argument("--train", action="store_true",
                   help="also train the classifier on the synthetic labels (overwrites data/models)")
    s.set_defaults(func=cmd_demo)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    # Log file name: the command, plus the sub-command for grouped commands (e.g. region-create).
    parts = argv[:2] if argv and argv[0] in {"region", "target", "fetch", "earth-ref", "world"} else argv[:1]
    name = "-".join(re.sub(r"[^a-z0-9]+", "", p.lower()) for p in parts) or "analog"
    with log.session(name, argv):
        args.func(args)


if __name__ == "__main__":
    sys.exit(main())
