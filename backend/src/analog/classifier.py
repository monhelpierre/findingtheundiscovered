"""Model B: positive-unlabeled (PU) bagging classifier.

Positives: cells inside known Earth analog sites (labels GeoJSON, property `analog_type`).
Unlabeled: every other cell. They are *not* true negatives (some may be undiscovered analogs),
so each bag samples a fresh random subset of them as "background" and the bags are averaged.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import shape
from sklearn.ensemble import RandomForestClassifier

from . import features as feat_mod
from . import paths
from .log import timed
from .region import Region

log = logging.getLogger(__name__)

BG = "background"
MODEL_PATH = paths.MODELS / "classifier.joblib"
EXCLUDE = {"elev_mean", "water_fraction"}  # elevation would memorise sites; water is a mask, not a trait


def load_labels(path: Path) -> list[dict]:
    gj = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for f in gj.get("features", []):
        props = f.get("properties") or {}
        if not props.get("analog_type"):
            continue
        out.append({"name": props.get("name", "?"), "analog_type": props["analog_type"],
                    "geom": shape(f["geometry"])})
    if not out:
        raise SystemExit(f"No features with an analog_type property in {path}")
    log.info("Labels: %d site(s) of %d type(s) from %s", len(out), len({o["analog_type"] for o in out}), path)
    return out


def labelled_frame(region_ids: list[str], labels: list[dict]) -> pd.DataFrame:
    frames = []
    for k, rid in enumerate(region_ids, 1):
        df = feat_mod.load(Region.load(rid)).copy()
        df["region"] = rid
        df["label"] = None
        df["site"] = None
        for lab in labels:
            inside = shapely.contains_xy(lab["geom"], df["lon"].to_numpy(), df["lat"].to_numpy())
            df.loc[inside, "label"] = lab["analog_type"]
            df.loc[inside, "site"] = lab["name"]
        n_lab = int(df["label"].notna().sum())
        sites = sorted(df.loc[df["label"].notna(), "site"].unique())
        log.info("  [%d/%d] %-20s %7s cells, %5s inside known sites%s", k, len(region_ids), rid,
                 f"{len(df):,}", f"{n_lab:,}", f" ({', '.join(sites)})" if sites else "")
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def _feature_cols(df: pd.DataFrame, exclude: set[str] = frozenset()) -> list[str]:
    meta = set(feat_mod.META_COLS) | {"region", "label", "site"} | EXCLUDE | set(exclude)
    pos = df[df["label"].notna()]
    cols = [c for c in df.columns if c not in meta and "__" not in c]
    return [c for c in cols if pos[c].notna().mean() >= 0.5]


def _fit_bags(P: pd.DataFrame, U: pd.DataFrame, cols, n_bags: int, seed: int):
    rng = np.random.default_rng(seed)
    n_u = min(len(U), max(3 * len(P), 500))
    models = []
    for b in range(n_bags):
        t0 = time.perf_counter()
        Ub = U.iloc[rng.choice(len(U), size=n_u, replace=False)]
        X = pd.concat([P[cols], Ub[cols]])
        y = np.concatenate([P["label"].to_numpy(), np.full(len(Ub), BG)])
        clf = RandomForestClassifier(n_estimators=200, min_samples_leaf=3, class_weight="balanced",
                                     n_jobs=-1, random_state=seed + b)
        models.append(clf.fit(X, y))
        log.debug("      bag %d/%d: %d positive + %d background cells, %.1f s",
                  b + 1, n_bags, len(P), len(Ub), time.perf_counter() - t0)
    return models


def _predict(models, X: pd.DataFrame, classes: list[str]) -> np.ndarray:
    proba = np.zeros((len(X), len(classes)))
    for m in models:
        p = m.predict_proba(X)
        for j, c in enumerate(m.classes_):
            proba[:, classes.index(c)] += p[:, j]
    return proba / len(models)


def _auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """Probability that a random positive outranks a random negative (ties count half)."""
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    from sklearn.metrics import roc_auc_score
    y = np.r_[np.ones(len(pos)), np.zeros(len(neg))]
    return float(roc_auc_score(y, np.r_[pos, neg]))


def _type_prob(models, X: pd.DataFrame, analog_type: str) -> np.ndarray:
    return _predict(models, X, [analog_type, BG])[:, 0]


def train(label_path: Path, region_ids: list[str], n_bags: int = 10, seed: int = 0,
          exclude: set[str] | None = None) -> dict:
    """One PU model per analog type (that type's sites vs. random unlabelled cells).

    Each type gets its own feature set (features present in >= 50% of its labelled cells), so
    e.g. desert types keep EMIT minerals while a boreal crater type uses what it has.
    Validation: for every type with >= 2 sites, hide one site, train on the others of that type,
    and measure how well the hidden site's cells outrank background cells (AUC), both against all
    background and against *bare* background (NDVI < 0.2), the harder and more honest test.
    """
    t_start = time.perf_counter()
    exclude = set(exclude or ())
    if exclude:
        log.info("Excluded from training: %s", ", ".join(sorted(exclude)))
    labels = load_labels(label_path)
    with timed(log, f"Loading features of {len(region_ids)} region(s)"):
        df = labelled_frame(region_ids, labels)
    P_all, U = df[df["label"].notna()], df[df["label"].isna()]
    if P_all.empty:
        raise SystemExit("No labelled cells: none of the regions overlap a site in the labels file.")
    if len(U) < 50:
        raise SystemExit("Too few unlabelled cells for background sampling; add a background region.")
    bare = U["ndvi"].lt(0.2).to_numpy() if "ndvi" in U else np.zeros(len(U), bool)
    types = sorted(P_all["label"].unique())
    log.info("Training data: %s labelled cells, %s unlabelled (background) cells, %d type(s)",
             f"{len(P_all):,}", f"{len(U):,}", len(types))

    per_type, report = {}, []
    for k, t in enumerate(types, 1):
        P = P_all[P_all["label"] == t]
        cols = _feature_cols(pd.concat([P, U]), exclude)
        if not cols:
            log.warning("[type %d/%d] %s: no feature present in >= 50%% of its cells; skipped", k, len(types), t)
            report.append({"type": t, "site": ", ".join(P["site"].unique()), "note": "no usable features"})
            continue
        sites = P["site"].unique().tolist()
        log.info("[type %d/%d] %s: %s cells in %d site(s); features: %s", k, len(types), t, f"{len(P):,}",
                 len(sites), ", ".join(cols))
        with timed(log, f"  fitting {n_bags} bags"):
            models = _fit_bags(P, U, cols, n_bags, seed)
        per_type[t] = {"models": models, "features": cols, "sites": sites, "n_positive": int(len(P))}
        if len(sites) < 2:
            log.info("  validation skipped: only one site of this type")
            report.append({"type": t, "site": sites[0], "note": "only one site of this type: label more to test it"})
            continue
        for j, site in enumerate(sites, 1):
            train_p, test = P[P["site"] != site], P[P["site"] == site]
            t0 = time.perf_counter()
            ms = _fit_bags(train_p, U, cols, max(3, n_bags // 3), seed + 100)
            p_site = _type_prob(ms, test[cols], t)
            p_bg = _type_prob(ms, U[cols], t)
            report.append({"type": t, "site": site, "cells": int(len(test)),
                           "analog_prob": round(float(p_site.mean()), 3),
                           "auc": round(_auc(p_site, p_bg), 3),
                           "auc_bare": round(_auc(p_site, p_bg[bare]), 3),
                           "beats_background_pct": round(100 * float((p_bg < p_site.mean()).mean()), 1)})
            report[-1]["top10"] = report[-1]["beats_background_pct"] >= 90  # brief's success criterion
            r = report[-1]
            log.info("  validation %d/%d: hide %-24s AUC %.3f, bare %.3f, "
                     "beats %5.1f%% of background -> top 10%%: %s (%.1f s)",
                     j, len(sites), site, r["auc"], r["auc_bare"], r["beats_background_pct"],
                     "pass" if r["top10"] else "fail", time.perf_counter() - t0)

    paths.MODELS.mkdir(parents=True, exist_ok=True)
    feats = sorted({c for v in per_type.values() for c in v["features"]})
    bundle = {"per_type": per_type, "features": feats, "classes": list(per_type) + [BG],
              "labels_file": str(label_path), "regions": region_ids,
              "n_positive": int(len(P_all)), "n_unlabeled": int(len(U)), "validation": report,
              "excluded_features": sorted(exclude)}
    joblib.dump(bundle, MODEL_PATH)
    log.info("Saved %s (%.1f MB). Training took %.1f s", MODEL_PATH, MODEL_PATH.stat().st_size / 1e6,
             time.perf_counter() - t_start)
    return bundle


def load_bundle():
    if not MODEL_PATH.exists():
        return None
    b = joblib.load(MODEL_PATH)
    return b if "per_type" in b else None  # models from older versions must be retrained


def predict(df: pd.DataFrame, bundle) -> pd.DataFrame:
    """prob__<type> from each type's model; analog_prob = the highest of them."""
    out = pd.DataFrame(index=df.index)
    for t, m in bundle["per_type"].items():
        X = df.reindex(columns=m["features"])
        p = _type_prob(m["models"], X, t)
        p[X.notna().mean(axis=1).to_numpy() < 0.5] = np.nan  # too few features for this model
        out[f"prob__{t}"] = p
    out["analog_prob"] = out.max(axis=1, skipna=True)
    return out
