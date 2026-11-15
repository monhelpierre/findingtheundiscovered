"""CRISM MTRDR summary products (Mars, PDS3 .IMG + .LBL) -> mineral-group shares for a target.

The summary product (`*_SU*_MTR3.IMG`) holds ~60 spectral parameters (OLINDEX3, D2300, ...).
GDAL reads it through the .LBL label; the band names come from the label's BAND_NAME list.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import rasterio
import yaml

from . import paths

SU_NAME = re.compile(r"^(?P<obs>[a-z]{3}[0-9a-f]{8})_\d{2}_su\d{3}[a-z]_mtr3\.(img|lbl)$", re.I)


def is_crism_summary(path: Path) -> bool:
    return bool(SU_NAME.match(Path(path).name))


def label_for(path: Path) -> Path:
    p = Path(path)
    for cand in (p.with_suffix(".lbl"), p.with_suffix(".LBL")):
        if cand.exists():
            return cand
    raise SystemExit(f"CRISM: the .LBL label for {p.name} is missing; download it next to the .IMG.")


def _label_value(text: str, key: str):
    m = re.search(rf"^\s*{key}\s*=\s*([^\r\n]+)", text, re.M)
    if not m:
        return None
    v = re.sub(r"<[^>]*>", "", m.group(1)).strip().strip('"')
    try:
        return float(v)
    except ValueError:
        return v


def read_summary(path: Path):
    """-> (parameters: {name: 2-D array with NaN}, info: {...}) for a CRISM summary product."""
    lbl = label_for(path)
    text = lbl.read_text(errors="ignore")
    m = re.search(r"BAND_NAME\s*=\s*\((.*?)\)", text, re.S)
    if not m:
        raise SystemExit(f"CRISM: no BAND_NAME list in {lbl.name}; is it a summary (SU) product?")
    names = [x.strip().strip('"') for x in m.group(1).split(",")]
    info = {
        "observation": SU_NAME.match(lbl.name)["obs"].upper() if SU_NAME.match(lbl.name) else lbl.stem,
        "min_lat": _label_value(text, "MINIMUM_LATITUDE"),
        "max_lat": _label_value(text, "MAXIMUM_LATITUDE"),
        "west_lon": _label_value(text, "WESTERNMOST_LONGITUDE"),
        "east_lon": _label_value(text, "EASTERNMOST_LONGITUDE"),
        "file": lbl.name,
    }
    return lbl, names, info


def group_shares(path: Path, cfg: dict | None = None):
    """Share of valid pixels with a confident detection, per mineral group."""
    cfg = cfg or yaml.safe_load((paths.CONFIG / "crism_groups.yaml").read_text(encoding="utf-8"))
    lbl, names, info = read_summary(path)
    shares, valid_any = {}, None
    with rasterio.open(lbl) as ds:
        def band(n):
            a = ds.read(names.index(n) + 1).astype("float64")
            a[(a < -1e30) | (a > 1e30) | (a == 65535)] = np.nan  # PDS null / missing values
            return a
        for group, rules in cfg["groups"].items():
            hit = None
            for param, thr in rules.items():
                if param not in names:
                    print(f"  CRISM: parameter {param} not in {lbl.name}; skipped")
                    continue
                a = band(param)
                valid = np.isfinite(a)
                valid_any = valid if valid_any is None else (valid_any | valid)
                h = np.where(valid, a > float(thr), False)
                hit = h if hit is None else (hit | h)
            if hit is not None and valid_any is not None and valid_any.any():
                shares[group] = float(hit[valid_any].mean())
    info["valid_pixels"] = int(valid_any.sum()) if valid_any is not None else 0
    return shares, info
