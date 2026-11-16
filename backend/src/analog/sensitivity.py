"""Weight sensitivity: does the ranking hold when the weights move?

For each target and each of its criteria, every feature weight of that criterion is multiplied by
0.8, then by 1.2, and the study regions are re-scored. The check counts how many of the target's
100 best eligible cells (outside the known-analog zones, at least one criterion met, score >= 0.2)
stay in the top 100. A robust ranking keeps most of them; the Moon-base sister project reports
96 to 100 for its four group weights.

    analog sensitivity              -> data/export/sensitivity.json (+ a table in the log)
"""

from __future__ import annotations

import copy
import json
import logging

import numpy as np
import pandas as pd

from . import features as feat_mod
from . import paths
from .criteria import target_criteria
from .export import EXCLUSION_KM, WORLD, km_to_nearest_analog, known_analogs
from .region import Region
from .similarity import score_all
from .targets import load_targets

log = logging.getLogger(__name__)

MIN_SCORE = 0.2


def top_cells(df: pd.DataFrame, tid: str, excluded: np.ndarray, n: int) -> set[int]:
    """Row positions of the n best eligible cells for one target."""
    s = df[f"score__{tid}"].to_numpy(dtype=float)
    ok = np.isfinite(s) & (s >= MIN_SCORE) & ~excluded & (df[f"met__{tid}"].to_numpy() > 0)
    idx = np.flatnonzero(ok)
    return set(idx[np.argsort(-s[idx], kind="stable")[:n]].tolist())


def reweight(target: dict, names: list[str], factor: float) -> dict:
    t = copy.deepcopy(target)
    for name in names:
        t["features"][name]["weight"] = float(t["features"][name].get("weight", 1.0)) * factor
    return t


def run(top: int = 100, factors: tuple[float, ...] = (0.8, 1.2), regions: list[Region] | None = None,
        targets: dict | None = None, write: bool = True) -> list[dict]:
    targets = targets if targets is not None else load_targets()
    regions = regions if regions is not None else [r for r in Region.all() if r.scope != WORLD]
    frames = [feat_mod.load(r) for r in regions if (r.dir / "features.parquet").exists()]
    if not frames:
        raise SystemExit("No region has features yet: run `analog features <region>` first.")
    df = pd.concat(frames, ignore_index=True)
    dist, _ = km_to_nearest_analog(df, known_analogs())
    excluded = dist < EXCLUSION_KM
    log.info("Weight sensitivity: %s cells in %d region(s), top %d, factors %s", f"{len(df):,}",
             len(frames), top, ", ".join(f"x{f:g}" for f in factors))
    rows: list[dict] = []
    for tid, t in targets.items():
        base = top_cells(score_all(df, {tid: t}), tid, excluded, top)
        if not base:
            log.info("  %-26s no eligible cell: skipped", tid)
            continue
        for k, names in sorted(target_criteria(t).items()):
            for f in factors:
                got = top_cells(score_all(df, {tid: reweight(t, names, f)}), tid, excluded, top)
                rows.append({"target": tid, "criterion": k, "features": names, "factor": f,
                             "top": len(base), "kept": len(base & got)})
        mine = [r for r in rows if r["target"] == tid]
        worst = min(mine, key=lambda r: r["kept"] / r["top"])
        log.info("  %-26s top %3d: keeps %d-%d (worst: criterion %d x%g)", tid, len(base),
                 min(r["kept"] for r in mine), max(r["kept"] for r in mine), worst["criterion"], worst["factor"])
    if write:
        paths.EXPORT.mkdir(parents=True, exist_ok=True)
        out = paths.EXPORT / "sensitivity.json"
        out.write_text(json.dumps({"top": top, "factors": list(factors), "min_score": MIN_SCORE,
                                   "exclusion_km": EXCLUSION_KM, "rows": rows}, indent=1))
        log.info("Wrote %s", out)
    return rows
