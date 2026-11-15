"""Moon/Mars target-site profiles, loaded from config/targets.yaml + config/targets.d/*.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml

from . import paths
from .earthref import resolve_target

MODES = {"near", "atleast", "atmost"}


def _files() -> list[Path]:
    files = [paths.CONFIG / "targets.yaml"]
    files += sorted((paths.CONFIG / "targets.d").glob("*.yaml"))
    return [f for f in files if f.exists()]


def load_targets() -> dict[str, dict]:
    targets: dict[str, dict] = {}
    for f in _files():
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for tid, t in (doc.get("targets") or {}).items():
            validate(tid, t, f)
            t["id"] = tid
            t["source_file"] = f.relative_to(paths.ROOT).as_posix()
            targets[tid] = resolve_target(t)  # later files override earlier ones
    return targets


def validate(tid: str, t: dict, f) -> None:
    where = f"target {tid!r} in {f}"
    if t.get("body") not in {"moon", "mars", "earth"}:
        raise SystemExit(f"{where}: body must be moon, mars or earth")
    feats = t.get("features")
    if not feats:
        raise SystemExit(f"{where}: needs a features: mapping")
    for name, spec in feats.items():
        if spec.get("percentile") is not None:
            if not 0 < float(spec["percentile"]) < 1:
                raise SystemExit(f"{where}: feature {name!r} percentile must be between 0 and 1 (e.g. 0.05)")
        elif "value" not in spec or "sigma" not in spec:
            raise SystemExit(f"{where}: feature {name!r} needs value and sigma (or percentile)")
        if float(spec.get("sigma", 1.0)) <= 0:
            raise SystemExit(f"{where}: feature {name!r} sigma must be > 0")
        if spec.get("mode", "near") not in MODES:
            raise SystemExit(f"{where}: feature {name!r} mode must be one of {sorted(MODES)}")


def write_user_target(tid: str, target: dict) -> Path:
    d = paths.CONFIG / "targets.d"
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{tid}.yaml"
    out.write_text(yaml.safe_dump({"targets": {tid: target}}, sort_keys=False, allow_unicode=True),
                   encoding="utf-8")
    return out
