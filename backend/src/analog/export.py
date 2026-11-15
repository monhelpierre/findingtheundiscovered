"""Score regions and export JSON for the web UI (data/export)."""

from __future__ import annotations

import json
import logging
import math
import shutil
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from shapely.geometry import shape

from . import classifier, criteria, paths
from . import features as feat_mod
from .region import Region
from .similarity import score_all
from .targets import load_targets

log = logging.getLogger(__name__)
WORLD = "world"  # Region.scope of the worldwide screen's tiles (world.py)


def score_region(region: Region, targets: dict | None = None, bundle=None) -> pd.DataFrame:
    df = feat_mod.load(region)
    targets = targets if targets is not None else load_targets()
    t0 = time.perf_counter()
    out = score_all(df, targets)
    log.debug("    similarity + criteria for %d target(s): %.1f s", len(targets), time.perf_counter() - t0)
    bundle = bundle if bundle is not None else classifier.load_bundle()
    if bundle is not None:
        t0 = time.perf_counter()
        out = pd.concat([out, classifier.predict(df, bundle)], axis=1)
        log.debug("    classifier (%d type model(s)): %.1f s", len(bundle["per_type"]), time.perf_counter() - t0)
    out.to_parquet(region.dir / "scores.parquet", index=False)
    return out


def _km(lat1, lon1, lat2, lon2):
    x = np.radians(lon2 - lon1) * np.cos(np.radians((lat1 + lat2) / 2))
    y = np.radians(lat2 - lat1)
    return 6371.0 * np.hypot(x, y)


EXCLUSION_KM = 50.0  # brief: no candidate within 50 km of a known analog
TIER_LETTER = {1: "A", 2: "B", 3: "C", 4: "D"}


def known_analogs() -> list[dict]:
    """Known analog sites (data/labels/*.geojson): used to train and test, never reported as results."""
    out = []
    for f in sorted(paths.LABELS.glob("*.geojson")) if paths.LABELS.is_dir() else []:
        try:
            gj = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for feat in gj.get("features", []):
            props = feat.get("properties") or {}
            if not props.get("analog_type") or not feat.get("geometry"):
                continue
            w, s, e, n = shape(feat["geometry"]).bounds
            out.append({"name": props.get("name", "?"), "analog_type": props["analog_type"],
                        "analog_for": props.get("analog_for"),
                        "criteria_known_for": props.get("criteria_known_for") or [],
                        "bbox": [round(w, 4), round(s, 4), round(e, 4), round(n, 4)],
                        "lat": round((s + n) / 2, 4), "lon": round((w + e) / 2, 4)})
    return out


def km_to_nearest_analog(df: pd.DataFrame, analogs: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """Distance (km) from each cell to the nearest known-analog outline (its bounding box), and its index."""
    lat, lon = df["lat"].to_numpy(float), df["lon"].to_numpy(float)
    best = np.full(len(df), np.inf)
    which = np.full(len(df), -1)
    for i, a in enumerate(analogs):
        w, s, e, n = a["bbox"]
        d = _km(lat, lon, np.clip(lat, s, n), np.clip(lon, w, e))
        closer = d < best
        best[closer], which[closer] = d[closer], i
    return best, which


def candidates(df: pd.DataFrame, col: str, n: int = 10, min_km: float = 15.0, min_score: float = 0.2,
               tid: str | None = None, excluded: np.ndarray | None = None):
    """Top cells with non-maximum suppression, so the list isn't 10 neighbouring cells.
    For a target (`tid`), a candidate must meet at least one criterion and carries its tier and
    criterion scores. Cells in `excluded` (near a known analog) are never candidates."""
    keep = df[col].notna() & (df[col] >= min_score)
    if excluded is not None:
        keep &= ~excluded
    if tid is not None and f"met__{tid}" in df:
        keep &= df[f"met__{tid}"] > 0
    d = df[keep]
    if tid is not None and f"tier__{tid}" in d:
        d = d.assign(_tier=d[f"tier__{tid}"]).sort_values(["_tier", col], ascending=[True, False]).head(3000)
    else:
        d = d.nlargest(3000, col)
    crit_cols = [c for c in df.columns if tid and c.startswith("crit") and c.endswith(f"__{tid}")]
    picked = []
    for r in d.to_dict("records"):
        lat, lon = r["lat"], r["lon"]
        if all(_km(lat, lon, p["lat"], p["lon"]) >= min_km for p in picked):
            c = {"lat": round(lat, 5), "lon": round(lon, 5), "score": round(float(r[col]), 3), "cell": int(r["cell"])}
            if tid is not None and f"tier__{tid}" in r:
                c.update(tier=TIER_LETTER.get(int(r[f"tier__{tid}"])), met=int(r[f"met__{tid}"]),
                         scored=int(r[f"nscored__{tid}"]),
                         criteria={k.split("__")[0][4:]: _clean(r[k], 3) for k in crit_cols})
            picked.append(c)
            if len(picked) >= n:
                break
    return picked


def _top(cands: list[dict], n: int = 25, spread_km: float | None = None) -> list[dict]:
    """Best candidates first (tier, then score); optionally at least spread_km apart."""
    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    out: list[dict] = []
    for c in sorted(cands, key=lambda c: (order.get(c.get("tier"), 4), -c["score"])):
        if spread_km and any(_km(c["lat"], c["lon"], p["lat"], p["lon"]) < spread_km for p in out):
            continue
        out.append(c)
        if len(out) >= n:
            break
    return out


def _clean(v, nd):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return round(float(v), nd)


def export(rescore: bool = True) -> dict:
    t_start = time.perf_counter()
    targets = load_targets()
    regions = Region.all()
    bundle = classifier.load_bundle()
    log.info("Export: %d region(s), %d target(s), classifier: %s, %s", len(regions), len(targets),
             f"{len(bundle['per_type'])} type model(s)" if bundle else "none",
             "re-scoring" if rescore else "reusing existing scores")
    (paths.EXPORT / "regions").mkdir(parents=True, exist_ok=True)
    # Drop files of regions that no longer exist (e.g. a deleted demo region), so they aren't published.
    # World tiles are re-published below only if they hold a top candidate.
    study_ids = {r.id for r in regions if r.scope != WORLD}
    for stale in (paths.EXPORT / "regions").glob("*.json"):
        if stale.stem not in study_ids:
            stale.unlink()
            if stale.stem not in {r.id for r in regions}:
                log.info("  removed stale export %s", stale.name)
    staging = paths.EXPORT / ".world_tiles"  # every world tile's cells; only the needed ones get published
    shutil.rmtree(staging, ignore_errors=True)
    n_world = sum(1 for r in regions if r.scope == WORLD)
    if n_world:
        staging.mkdir(parents=True)

    index = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "targets": [
            {k: t.get(k) for k in ("id", "name", "body", "category", "description", "lat", "lon",
                                   "reference_url", "notes", "features", "source_file")}
            for t in targets.values()
        ],
        "features": {},
        "regions": [],
        "candidates": {tid: [] for tid in targets},
        # Worldwide screen (world tiles from `analog world import`): the app's "Worldwide" checkbox
        "candidates_world": {tid: [] for tid in targets} if n_world else {},
        "world": None,
        "classifier": None,
        **criteria.describe(),
        "known_analogs": known_analogs(),
        "exclusion_km": EXCLUSION_KM,
    }
    analogs = index["known_analogs"]
    log.info("Known analogs: %d (cells within %.0f km are never candidates)", len(analogs), EXCLUSION_KM)
    if bundle is not None:
        index["classifier"] = {
            "classes": [c for c in bundle["classes"] if c != classifier.BG],
            "features": bundle["features"], "regions": bundle["regions"],
            "n_positive": bundle["n_positive"],
            # Brief: a hidden known analog should rank in the top 10% (beat >= 90% of background cells).
            "validation": [{**v, "top10": v["beats_background_pct"] >= 90} if "beats_background_pct" in v else v
                           for v in bundle["validation"]],
        }
        index["candidates"]["__classifier__"] = []
        if n_world:
            index["candidates_world"]["__classifier__"] = []
    world_entries: dict[str, dict] = {}
    world_cells = 0

    for k, r in enumerate(regions, 1):
        if not (r.dir / "features.parquet").exists():
            log.warning("  [%d/%d] skip %s: no features yet (run: analog features %s)", k, len(regions), r.id, r.id)
            continue
        t_region = time.perf_counter()
        df = score_region(r, targets, bundle) if rescore or not (r.dir / "scores.parquet").exists() \
            else pd.read_parquet(r.dir / "scores.parquet")
        meta = feat_mod.feature_meta(df.columns)
        index["features"].update(meta)
        feat_cols = list(meta)
        score_cols = [c for c in df.columns if c.startswith(("score__", "prob__"))] + \
            (["analog_prob"] if "analog_prob" in df.columns else [])
        cols = {"cell": df["cell"].astype(int).tolist(),
                "lon": [round(v, 5) for v in df["lon"]], "lat": [round(v, 5) for v in df["lat"]]}
        for c in feat_cols:
            cols[c] = [_clean(v, 3) for v in df[c]]
        for c in (c for c in df.columns if c.startswith("rel__")):  # Earth normal scores (percentile features)
            cols[c] = [_clean(v, 3) for v in df[c]]
        for c in score_cols:
            cols[c] = [_clean(v, 3) for v in df[c]]
        dist, _ = km_to_nearest_analog(df, analogs)
        excluded = dist < EXCLUSION_KM
        cols["analog_km"] = [None if not np.isfinite(v) else round(float(v), 1) for v in dist]
        world = r.scope == WORLD
        out_dir = staging if world else paths.EXPORT / "regions"
        (out_dir / f"{r.id}.json").write_text(json.dumps({
            "id": r.id, "title": r.title, "bbox": r.bbox, "cell_deg": r.cell_deg, "scope": r.scope,
            "layers": sorted(r.layers), "n_cells": len(df), "columns": cols,
        }, separators=(",", ":")))

        entry = {"id": r.id, "title": r.title, "bbox": r.bbox, "cell_deg": r.cell_deg, "scope": r.scope,
                 "layers": sorted(r.layers), "n_cells": len(df), "features": feat_cols,
                 "excluded_cells": int(excluded.sum())}
        if world:
            world_entries[r.id] = entry
            world_cells += len(df)
        else:
            index["regions"].append(entry)
        bucket = index["candidates_world"] if world else index["candidates"]
        n_cands = 0
        for tid in targets:
            found = candidates(df, f"score__{tid}", tid=tid, excluded=excluded)
            n_cands += len(found)
            for c in found:
                bucket[tid].append({**c, "region": r.id})
            if f"tier__{tid}" in df:
                tiers = df[f"tier__{tid}"].value_counts()
                log.debug("    %-26s scored %6s cells; tiers A %s B %s C %s D %s; %d candidate(s)", tid,
                          f"{int(df[f'score__{tid}'].notna().sum()):,}",
                          *(int(tiers.get(n, 0)) for n in (1, 2, 3, 4)), len(found))
        if "analog_prob" in df.columns:
            for c in candidates(df, "analog_prob", excluded=excluded):
                bucket["__classifier__"].append({**c, "region": r.id})
        log.info("  [%d/%d] %-20s %7s cells, %6s excluded (near a known analog), %2d candidate(s), %.1f s",
                 k, len(regions), r.id, f"{len(df):,}", f"{int(excluded.sum()):,}", n_cands,
                 time.perf_counter() - t_region)
        log.debug("    features: %s", ", ".join(feat_cols))

    for k in index["candidates"]:
        index["candidates"][k] = _top(index["candidates"][k])
    if n_world:
        published = set()
        for k in index["candidates_world"]:
            # tiles meet at their edges: keep candidates 15 km apart across tiles too
            index["candidates_world"][k] = _top(index["candidates_world"][k], spread_km=15.0)
            published |= {c["region"] for c in index["candidates_world"][k]}
        for rid in sorted(published):
            shutil.move(str(staging / f"{rid}.json"), str(paths.EXPORT / "regions" / f"{rid}.json"))
            index["regions"].append(world_entries[rid])
        shutil.rmtree(staging, ignore_errors=True)
        index["world"] = {"tiles": n_world, "cells": world_cells, "published_tiles": len(published)}
        log.info("World screen: %d tile(s), %s cells; %d tile(s) hold a top candidate and are published",
                 n_world, f"{world_cells:,}", len(published))
    (paths.EXPORT / "index.json").write_text(json.dumps(index, indent=1))
    log.info("Candidates per target (after the %.0f km exclusion):", EXCLUSION_KM)
    for label, group in (("", index["candidates"]), ("worldwide ", index["candidates_world"])):
        for k, cs in group.items():
            tiers = {t: sum(1 for c in cs if c.get("tier") == t) for t in "ABCD"}
            detail = "" if k == "__classifier__" else \
                "  (" + ", ".join(f"{t}: {n}" for t, n in tiers.items() if n) + ")"
            log.info("  %s%-26s %2d%s", label, k, len(cs), detail)
    size = sum(f.stat().st_size for f in paths.EXPORT.rglob("*.json")) / 1e6
    log.info("Wrote %s (%.1f MB) in %.1f s", paths.EXPORT, size, time.perf_counter() - t_start)
    publish_to_web()
    return index


def publish_to_web() -> None:
    """Copy data/export/ into frontend/public/data/, where the web app loads it (and Firebase serves it)."""
    web = paths.FRONTEND
    if not web.is_dir():
        return
    dst = web / "public" / "data"
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(paths.EXPORT, dst)
    log.info("Copied to %s (commit it to update the deployed site)", dst)
