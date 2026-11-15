"""The project brief's 8 analog criteria, per-criterion scores, and tiers.

Each target feature belongs to one criterion (FEATURE_CRITERION, or `criterion: N` in the target's
YAML). A criterion's score is the weighted geometric mean of its features' similarities s_i (same
formula as the overall score, restricted to that criterion), from 0 (nothing alike) to 1 (identical).

    criterion score  >= 0.85 strong match | >= 0.70 match (counts as met) | >= 0.50 near miss | else mismatch
    match ratio      = criteria met / criteria scored for that cell
    tier             A >= 0.75 | B >= 0.50 | C >= 0.25 | D > 0 (at least one criterion met)

A criterion is "scored" in a cell when the target has at least one feature for it and the cell has
data for that feature. NDVI is not a criterion: it is a gate (bare ground) handled by the overall score.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .similarity import feature_z

CRITERIA: dict[int, tuple[str, str]] = {
    1: ("Geology", "Geology, mineralogy and surface materials"),
    2: ("Terrain", "Terrain, relief, roughness and landforms"),
    3: ("Thermal", "Temperature, illumination and environmental extremes"),
    4: ("Water & ice", "Water, ice, permafrost and other resources"),
    5: ("Surface processes", "Dust, wind, erosion and other surface processes"),
    6: ("Biology", "Biology, extremophiles, habitability (dossiers only)"),
    7: ("Subsurface", "Caves, lava tubes and other subsurface features (dossiers only)"),
    8: ("Operations", "Landing, mobility, navigation and construction"),
}
# Criteria that apply to each body (brief: Moon 5 scored, Mars 6; 6 and 7 are for the dossiers).
APPLIES: dict[str, list[int]] = {"moon": [1, 2, 3, 4, 8], "mars": [1, 2, 3, 4, 5, 8], "earth": [1, 2, 3, 4, 5, 8]}

FEATURE_CRITERION: dict[str, int] = {
    "albedo": 1, "clay_index": 1, "ferric_index": 1,
    # Terrain (2): slope, roughness and relief describe the land's shape (team schema: slope in 2).
    "slope_mean": 2, "slope_p75": 2, "relief": 2, "roughness": 2,
    "temp_range": 3, "tmin_k": 3, "tday_range_k": 3, "tyear_range_k": 3, "sun_elev_deg": 3,
    "water_fraction": 4, "hydrogen_or_soilmoist": 4, "ice_flag": 4,
    "dust_aod": 5, "wind_ms": 5,
    "aridity_index": 6,  # biology: dossiers only (criterion 6 isn't scored)
    # Operations (8): share of ground at 0-15 deg, where landing, driving and building are possible.
    "gentle_slope_frac": 8,
}
GATES = {"ndvi"}  # not a criterion: required bare ground

STRONG, MET, NEAR = 0.85, 0.70, 0.50
TIERS = [(0.75, "A", "Strong match"), (0.50, "B", "Good analog"),
         (0.25, "C", "Partial analog"), (0.0, "D", "Single-criterion analog")]


def criterion_of(name: str, spec: dict) -> int | None:
    if spec.get("criterion") is not None:
        return int(spec["criterion"])
    if name in GATES:
        return None
    if name in FEATURE_CRITERION:
        return FEATURE_CRITERION[name]
    if name.startswith("min_"):
        return 1
    return None


def target_criteria(target: dict) -> dict[int, list[str]]:
    """Criterion -> the target's features in it (only criteria that apply to the target's body)."""
    applies = APPLIES.get(target.get("body", "earth"), APPLIES["earth"])
    out: dict[int, list[str]] = {}
    for name, spec in target["features"].items():
        k = criterion_of(name, spec)
        if k in applies:
            out.setdefault(k, []).append(name)
    return out


def band(score: float) -> str:
    if not np.isfinite(score):
        return "no data"
    return "strong" if score >= STRONG else "match" if score >= MET else "near miss" if score >= NEAR else "mismatch"


def tier_letter(met: int, scored: int) -> str | None:
    if scored <= 0 or met <= 0:
        return None
    ratio = met / scored
    return next(letter for lo, letter, _ in TIERS if ratio >= lo)


def criteria_columns(df: pd.DataFrame, tid: str, target: dict, overall: np.ndarray) -> dict[str, np.ndarray]:
    """crit<k>__<tid> for each applicable criterion, plus met__, nscored__ and tier__ (1=A .. 4=D, 0=none).
    Cells without an overall score (water, missing gate feature, low coverage) get no criteria either."""
    gate = np.isfinite(overall)
    # Gate features (NDVI: bare ground) must themselves be met, or vegetated land could reach tier A.
    for name, spec in target["features"].items():
        if criterion_of(name, spec) is None and name in GATES:
            x = df[name].to_numpy(dtype=float) if name in df else np.full(len(df), np.nan)
            with np.errstate(invalid="ignore"):
                gate &= np.exp(-0.5 * feature_z(x, spec) ** 2) >= MET
    cols: dict[str, np.ndarray] = {}
    met = np.zeros(len(df), dtype=int)
    scored = np.zeros(len(df), dtype=int)
    for k, names in sorted(target_criteria(target).items()):
        num, wsum = np.zeros(len(df)), np.zeros(len(df))
        for name in names:
            spec = target["features"][name]
            col = spec.get("column", name)
            if col not in df.columns:
                continue
            x = df[col].to_numpy(dtype=float)
            ok = np.isfinite(x)
            w = float(spec.get("weight", 1.0))
            num[ok] += w * feature_z(x[ok], spec) ** 2
            wsum[ok] += w
        with np.errstate(invalid="ignore", divide="ignore"):
            s = np.exp(-0.5 * num / wsum)
        s[(wsum == 0) | ~gate] = np.nan
        cols[f"crit{k}__{tid}"] = s
        has = np.isfinite(s)
        scored += has
        met += has & (s >= MET)
    ratio = np.where(scored > 0, met / np.maximum(scored, 1), 0.0)
    tier = np.zeros(len(df), dtype=int)
    for n, (lo, _, _) in reversed(list(enumerate(TIERS, start=1))):
        tier[(met > 0) & (ratio >= lo)] = n
    cols[f"met__{tid}"] = met
    cols[f"nscored__{tid}"] = scored
    cols[f"tier__{tid}"] = tier
    return cols


def describe() -> dict:
    """Definitions exported for the web app (so it computes the same thing in the browser)."""
    return {
        "criteria": {str(k): {"short": s, "label": lab} for k, (s, lab) in CRITERIA.items()},
        "applies": APPLIES, "feature_criterion": FEATURE_CRITERION, "gates": sorted(GATES),
        "thresholds": {"strong": STRONG, "met": MET, "near": NEAR},
        "tiers": [{"min_ratio": lo, "tier": t, "label": lab} for lo, t, lab in TIERS],
    }
