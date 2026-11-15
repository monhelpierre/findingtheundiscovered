"""Relative (percentile) features: compare where a site ranks on its own world.

Earth can't reach the Moon's -230 C or Mars's dust storms, so for some features the absolute value
is the wrong comparison. Instead, a target says where it ranks on its own body ("Malapert is among
the coldest 5% of the Moon": percentile 0.05) and an Earth cell is compared by where it ranks on
Earth's land. Both ranks go through the inverse normal CDF so they can be compared like z-scores
(the team plan's `RELATIVE` features, finding-the-undiscovered/src/scoring.py):

    z_earth  = Phi^-1(rank of x among Earth's land, 0..1)      column rel__<feature>
    z_target = Phi^-1(target percentile)                       spec value
    z        = (z_earth - z_target) / sigma                    then the usual s = exp(-z^2/2)

Ranks are clipped to 0.001..0.999 (z within +-3.09). sigma defaults to 1: one standard normal unit
apart gives a similarity of 0.61, e.g. 5th vs 26th percentile.

Earth's ranks come from config/earth_reference.json: for each feature, 1001 quantiles of its values
over Earth's land, area-weighted (cos latitude). Build it with `analog earth-ref build`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import paths

log = logging.getLogger(__name__)

N_QUANTILES = 1001
P_CLIP = (0.001, 0.999)
DEFAULT_SIGMA = 1.0


def reference_path() -> Path:
    return paths.CONFIG / "earth_reference.json"


# ---- inverse normal CDF (Acklam's rational approximation, relative error < 1.2e-9) -------------
# Implemented here so the API image needs no scipy. Same constants as frontend/lib/similarity.ts.
_A = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02, 1.383577518672690e02,
      -3.066479806614716e01, 2.506628277459239e00]
_B = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02, 6.680131188771972e01,
      -1.328068155288572e01]
_C = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00, -2.549732539343734e00,
      4.374664141464968e00, 2.938163982698783e00]
_D = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]
_P_LOW = 0.02425


def norm_ppf(p):
    """Inverse of the standard normal CDF, for p in (0, 1). Works on scalars and arrays."""
    p = np.asarray(p, dtype=float)
    out = np.full(p.shape, np.nan)
    lo = (p > 0) & (p < _P_LOW)
    hi = (p > 1 - _P_LOW) & (p < 1)
    mid = (p >= _P_LOW) & (p <= 1 - _P_LOW)
    if lo.any():
        q = np.sqrt(-2 * np.log(p[lo]))
        out[lo] = (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
                  ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1)
    if hi.any():
        q = np.sqrt(-2 * np.log(1 - p[hi]))
        out[hi] = -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
                   ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1)
    if mid.any():
        q = p[mid] - 0.5
        r = q * q
        out[mid] = (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q / \
                   (((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1)
    return out if out.ndim else float(out)


def percentile_z(p: float) -> float:
    return float(norm_ppf(min(max(float(p), P_CLIP[0]), P_CLIP[1])))


# ---- target specs ---------------------------------------------------------------------------------

def resolve_spec(name: str, spec: dict) -> dict:
    """A copy of a feature spec ready for scoring. `percentile: p` specs get value = Phi^-1(p),
    sigma (default 1) and column = rel__<name>; other specs are returned unchanged. Idempotent."""
    if spec.get("percentile") is None:
        return spec
    out = dict(spec)
    out["value"] = round(percentile_z(spec["percentile"]), 6)
    out.setdefault("sigma", DEFAULT_SIGMA)
    out["column"] = f"rel__{name}"
    return out


def resolve_target(target: dict) -> dict:
    t = dict(target)
    t["features"] = {n: resolve_spec(n, s) for n, s in target["features"].items()}
    return t


def relative_features(targets: dict[str, dict]) -> set[str]:
    return {n for t in targets.values() for n, s in t["features"].items() if s.get("percentile") is not None}


# ---- Earth reference ------------------------------------------------------------------------------

@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_reference() -> dict:
    p = reference_path()
    if not p.exists():
        return {"features": {}}
    return _load(str(p), p.stat().st_mtime)


def earth_rank(name: str, x: np.ndarray) -> np.ndarray | None:
    """Rank (0..1) of each value among Earth's land for that feature (mid-rank for ties), or None
    if the reference has no such feature."""
    ref = load_reference()["features"].get(name)
    if ref is None:
        return None
    q = np.asarray(ref["quantiles"], dtype=float)
    x = np.asarray(x, dtype=float)
    left = np.searchsorted(q, x, side="left")
    right = np.searchsorted(q, x, side="right")
    rank = (left + right) / 2 / len(q)
    rank[~np.isfinite(x)] = np.nan
    return rank


def add_relative_columns(df, names) -> dict[str, np.ndarray]:
    """rel__<name> columns (Earth normal scores) for the given features, where df has them."""
    cols = {}
    missing = []
    for name in sorted(names):
        if name not in df.columns or f"rel__{name}" in df.columns:
            continue
        rank = earth_rank(name, df[name].to_numpy(dtype=float))
        if rank is None:
            missing.append(name)
            continue
        z = np.full(len(rank), np.nan)
        ok = np.isfinite(rank)
        z[ok] = norm_ppf(np.clip(rank[ok], *P_CLIP))
        cols[f"rel__{name}"] = z
    if missing:
        log.warning("No Earth reference for %s: those percentile features are skipped "
                    "(build it with: analog earth-ref build)", ", ".join(missing))
    return cols


def weighted_quantiles(values: np.ndarray, weights: np.ndarray, n: int = N_QUANTILES) -> np.ndarray:
    v, w = np.asarray(values, float), np.asarray(weights, float)
    ok = np.isfinite(v) & np.isfinite(w) & (w > 0)
    v, w = v[ok], w[ok]
    order = np.argsort(v)
    v, w = v[order], w[order]
    cw = np.cumsum(w) - 0.5 * w
    cw /= w.sum()
    return np.interp(np.linspace(0, 1, n), cw, v)


def write_reference(samples: dict[str, tuple[np.ndarray, np.ndarray]], source: str) -> Path:
    """samples: {feature: (values, area weights)} over Earth's land."""
    feats = {}
    for name, (v, w) in sorted(samples.items()):
        ok = np.isfinite(v)
        if ok.sum() < 50:
            log.warning("  %s: only %d values, skipped", name, int(ok.sum()))
            continue
        q = weighted_quantiles(v, w)
        feats[name] = {"n": int(ok.sum()), "quantiles": [float(f"{x:.6g}") for x in q]}
        log.info("  %-22s n=%5d  p5 %.4g  p50 %.4g  p95 %.4g", name, ok.sum(), q[50], q[500], q[950])
    doc = {
        "description": "Earth land quantiles per feature (area-weighted) for percentile target features. "
                       "Built by `analog earth-ref build`; see src/analog/earthref.py.",
        "source": source,
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_quantiles": N_QUANTILES,
        "features": feats,
    }
    p = reference_path()
    p.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    _load.cache_clear()
    return p


def from_regions(regions) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Samples from the regions' feature tables (cells weighted by cos latitude). Representative of
    Earth only if the regions cover Earth, e.g. the Earth Engine whole-land export."""
    from . import features as feat_mod

    vals: dict[str, list] = {}
    wts: dict[str, list] = {}
    for r in regions:
        if not (r.dir / "features.parquet").exists():
            continue
        df = feat_mod.load(r)
        w = np.cos(np.radians(df["lat"].to_numpy(dtype=float)))
        for c in feat_mod.feature_meta(df.columns):
            vals.setdefault(c, []).append(df[c].to_numpy(dtype=float))
            wts.setdefault(c, []).append(w)
    return {c: (np.concatenate(vals[c]), np.concatenate(wts[c])) for c in vals}

