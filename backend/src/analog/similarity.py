"""Model A: Analog Similarity Index.

For each feature i with target value p_i, tolerance sigma_i and weight w_i:
    z_i = (x_i - p_i) / sigma_i          mode "near"
    z_i = max(0, (p_i - x_i) / sigma_i)   mode "atleast" (x should be >= p)
    z_i = max(0, (x_i - p_i) / sigma_i)   mode "atmost"  (x should be <= p)
    s_i = exp(-z_i^2 / 2)                 per-feature similarity, shown in the UI

Percentile features (`percentile: p` in the target, see earthref.py) use the same formula on normal
scores: x = Phi^-1(the cell's rank on Earth's land) from column rel__<feature>, p_i = Phi^-1(p).
    score = exp(-1/2 * sum(w_i z_i^2) / sum(w_i))   (weighted geometric mean of s_i)

Features missing in a cell are skipped; `coverage` is the share of the target's total weight that
was available. Cells below `min_coverage` get no score.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import earthref


def feature_z(x: np.ndarray, spec: dict) -> np.ndarray:
    v, sg = float(spec["value"]), float(spec["sigma"])
    mode = spec.get("mode", "near")
    if mode == "atleast":
        return np.maximum(0.0, (v - x) / sg)
    if mode == "atmost":
        return np.maximum(0.0, (x - v) / sg)
    return (x - v) / sg


def score_target(df: pd.DataFrame, target: dict, min_coverage: float = 0.5):
    feats = target["features"]
    total_w = sum(float(s.get("weight", 1.0)) for s in feats.values())
    num = np.zeros(len(df))
    wsum = np.zeros(len(df))
    for name, spec in feats.items():
        col = spec.get("column", name)
        if col not in df.columns:
            continue
        x = df[col].to_numpy(dtype=float)
        ok = np.isfinite(x)
        w = float(spec.get("weight", 1.0))
        z = feature_z(x[ok], spec)
        num[ok] += w * z**2
        wsum[ok] += w
    coverage = wsum / total_w if total_w else np.zeros(len(df))
    with np.errstate(invalid="ignore", divide="ignore"):
        score = np.exp(-0.5 * num / wsum)
    score[(coverage < min_coverage) | (wsum == 0)] = np.nan
    # A `required: true` feature must be present: skipping it would silently drop a check
    # (e.g. without NDVI, vegetated land would be judged on terrain alone and could look lunar).
    for name, spec in feats.items():
        if spec.get("required"):
            col = spec.get("column", name)
            have = df[col].notna().to_numpy() if col in df.columns else np.zeros(len(df), bool)
            score[~have] = np.nan
    return score, coverage


MAX_WATER = 0.5  # cells that are more than half open water are not scored


def score_all(df: pd.DataFrame, targets: dict[str, dict], min_coverage: float = 0.5) -> pd.DataFrame:
    targets = {tid: earthref.resolve_target(t) for tid, t in targets.items()}
    rel = earthref.add_relative_columns(df, earthref.relative_features(targets))
    if rel:  # Earth normal scores for percentile features: scored, and exported for the web app
        df = pd.concat([df, pd.DataFrame(rel, index=df.index)], axis=1)
    out = df.copy()
    score_cols = []
    wet = np.zeros(len(df), dtype=bool)
    if "water_fraction" in df:
        wet |= df["water_fraction"].to_numpy(dtype=float) > MAX_WATER
    if "elev_mean" in df and "relief" in df:
        # Backup where HLS has no water info (clouds, scene gaps): SRTM's open sea is flat at ~0 m.
        wet |= (np.abs(df["elev_mean"].to_numpy(dtype=float)) < 1) & (df["relief"].to_numpy(dtype=float) < 1)
    from .criteria import criteria_columns  # local import: criteria imports feature_z from here

    extra = {}
    for tid, t in targets.items():
        s, c = score_target(df, t, min_coverage)
        s[wet] = np.nan
        out[f"score__{tid}"] = s
        out[f"cov__{tid}"] = c
        score_cols.append(f"score__{tid}")
        extra.update(criteria_columns(df, tid, t, s))
    out = pd.concat([out, pd.DataFrame(extra, index=out.index)], axis=1)
    if score_cols:
        m = out[score_cols]
        has = m.notna().any(axis=1)
        out["best_target"] = None
        out.loc[has, "best_target"] = m[has].idxmax(axis=1).str.removeprefix("score__")
        out["best_score"] = m.max(axis=1)
    return out
