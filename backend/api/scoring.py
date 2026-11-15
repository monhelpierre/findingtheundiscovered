"""Re-score a region for one target with custom weights (the brief's weight sliders).

Uses the pipeline's own code (src/analog/similarity.py and criteria.py), so the numbers are
identical to `analog export` when the weights are unchanged.
"""

from __future__ import annotations

import copy
import math

import numpy as np
import pandas as pd

from analog.similarity import score_all

from . import data
from .models import KnownAnalog, Target

EXCLUSION_KM = 50.0
TIER_LETTER = {1: "A", 2: "B", 3: "C", 4: "D"}
MAX_WEIGHT = 10.0


def targets() -> list[dict]:
    """Active targets: the database's (admin-editable) if it has any, else the exported ones."""
    db = [t.as_dict() for t in Target.objects.filter(active=True)]
    return db or list(data.index().get("targets", []))


def get_target(target_id: str) -> dict | None:
    return next((t for t in targets() if t["id"] == target_id), None)


def known_analogs() -> list[dict]:
    db = [a.as_dict() for a in KnownAnalog.objects.all()]
    return db or list(data.index().get("known_analogs", []))


def apply_weights(target: dict, weights: dict[str, float]) -> dict:
    """Copy of the target with some feature weights replaced. Unknown features are ignored."""
    t = copy.deepcopy(target)
    for name, w in weights.items():
        if name in t["features"]:
            t["features"][name]["weight"] = float(w)
    return t


def parse_weights(params) -> dict[str, float]:
    """Weights from query parameters like ?w.slope_mean=2&w.albedo=0.5 (0 switches a feature off)."""
    out = {}
    for key, raw in params.items():
        if not key.startswith("w."):
            continue
        try:
            w = float(raw)
        except ValueError as e:
            raise ValueError(f"weight for {key[2:]} is not a number") from e
        if not (0 <= w <= MAX_WEIGHT) or math.isnan(w):
            raise ValueError(f"weight for {key[2:]} must be between 0 and {MAX_WEIGHT:g}")
        out[key[2:]] = w
    return out


def _km(lat1, lon1, lat2, lon2):
    x = np.radians(lon2 - lon1) * np.cos(np.radians((lat1 + lat2) / 2))
    y = np.radians(lat2 - lat1)
    return 6371.0 * np.hypot(x, y)


def km_to_nearest_analog(df: pd.DataFrame, analogs: list[dict]) -> np.ndarray:
    lat, lon = df["lat"].to_numpy(float), df["lon"].to_numpy(float)
    best = np.full(len(df), np.inf)
    for a in analogs:
        w, s, e, n = a["bbox"]
        best = np.minimum(best, _km(lat, lon, np.clip(lat, s, n), np.clip(lon, w, e)))
    return best


def _r(v, nd=3):
    return None if v is None or not np.isfinite(v) else round(float(v), nd)


def candidates(df: pd.DataFrame, tid: str, excluded: np.ndarray, n: int = 10,
               min_km: float = 15.0, min_score: float = 0.2) -> list[dict]:
    """Same rules as the export: outside the exclusion zone, >= 1 criterion met, ordered by tier then score,
    at least 15 km apart."""
    col = f"score__{tid}"
    keep = df[col].notna() & (df[col] >= min_score) & ~excluded & (df[f"met__{tid}"] > 0)
    d = df[keep].sort_values([f"tier__{tid}", col], ascending=[True, False]).head(3000)
    crit_cols = [c for c in df.columns if c.startswith("crit") and c.endswith(f"__{tid}")]
    picked: list[dict] = []
    for r in d.to_dict("records"):
        if all(_km(r["lat"], r["lon"], p["lat"], p["lon"]) >= min_km for p in picked):
            picked.append({
                "lat": round(r["lat"], 5), "lon": round(r["lon"], 5), "score": round(float(r[col]), 3),
                "cell": int(r["cell"]), "tier": TIER_LETTER.get(int(r[f"tier__{tid}"])),
                "met": int(r[f"met__{tid}"]), "scored": int(r[f"nscored__{tid}"]),
                "criteria": {k.split("__")[0][4:]: _r(r[k]) for k in crit_cols},
            })
            if len(picked) >= n:
                break
    return picked


def score_region(region_id: str, target: dict) -> dict:
    """Score every cell of a region for one target: overall score, per-criterion scores, tier."""
    df = data.region_frame(region_id)
    tid = target["id"]
    out = score_all(df, {tid: target})
    excluded = km_to_nearest_analog(df, known_analogs()) < EXCLUSION_KM
    crit_cols = sorted(c for c in out.columns if c.startswith("crit") and c.endswith(f"__{tid}"))
    tier = out[f"tier__{tid}"].astype(int)
    return {
        "region": region_id,
        "target": tid,
        "weights": {k: v.get("weight", 1.0) for k, v in target["features"].items()},
        "exclusion_km": EXCLUSION_KM,
        "columns": {
            "score": [_r(v) for v in out[f"score__{tid}"]],
            "tier": [TIER_LETTER.get(int(v)) for v in tier],
            "met": out[f"met__{tid}"].astype(int).tolist(),
            "scored": out[f"nscored__{tid}"].astype(int).tolist(),
            **{f"crit{c.split('__')[0][4:]}": [_r(v) for v in out[c]] for c in crit_cols},
        },
        "candidates": [{**c, "region": region_id} for c in candidates(out, tid, excluded)],
    }
