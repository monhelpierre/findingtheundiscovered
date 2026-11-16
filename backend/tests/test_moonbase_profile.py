"""Features and outputs taken from the Moon-base sister project: low-sun days, the Moon-base sites
import, and the weight-sensitivity check."""

import json

import numpy as np
import pandas as pd
import pytest

from analog import criteria, features, illumination, moonbase, paths, sensitivity


def test_low_sun_days_follow_latitude():
    d = illumination.low_sun_days([0, 45, 56, 60, 66.43, 66.56, -66.56, 70])
    assert list(d[:3]) == [0, 0, 0], "the noon Sun is never that low below ~56 deg"
    assert d[3] > 0 and d[4] == 106, "same value as the sister project at its best site (66.43 N)"
    assert d[5] == 107 and abs(d[6] - 107) <= 1, "Arctic and Antarctic Circles (the Sun's path is not quite symmetric)"
    assert d[7] < d[5], "beyond the circle polar night replaces low-sun days"


def test_new_features_are_described_and_grouped():
    for name, k in [("low_sun_days", 3), ("permafrost_class", 4), ("passable_2km", 8),
                    ("passable_10km", 8), ("passable_50km", 8)]:
        assert name in features.FEATURES
        assert criteria.FEATURE_CRITERION[name] == k


def test_moonbase_import(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "EXPORT", tmp_path / "export")
    df = pd.DataFrame({"site_code": ["MOON-0001", "MOON-0002"], "country": ["Greenland", None],
                       "continent": ["North America", "Asia"], "tier": ["Tier 2", "Tier 3"],
                       "lat": [66.43041, 70.1], "lon": [-49.54213, 140.2], "S": [0.8612, 0.4],
                       "n_met": [3, 1], "n_scored": [5, 5], "s1": [0.5, np.nan], "low_sun_days": [106, 90],
                       "best_on_continent": [True, False], "rank": [1, 2], "extra_column": [1, 2]})
    src = tmp_path / "sites.parquet"
    df.to_parquet(src, index=False)
    out = moonbase.import_sites(tmp_path)  # finds sites.parquet in the folder
    d = json.loads(out.read_text())
    assert d["n"] == 2 and "extra_column" not in d["columns"]
    assert d["columns"]["lat"] == [66.4304, 70.1] and d["columns"]["S"] == [0.861, 0.4]
    assert d["columns"]["country"] == ["Greenland", None] and d["columns"]["s1"] == [0.5, None]
    assert d["columns"]["best_on_continent"] == [True, False]


def test_moonbase_refuses_synthetic_sites(tmp_path):
    pd.DataFrame({"lat": [1.0], "lon": [2.0], "tier": ["Tier 1"], "S": [0.9], "synthetic": [True]}) \
        .to_csv(tmp_path / "sites_moon.csv", index=False)
    with pytest.raises(SystemExit, match="SYNTHETIC"):
        moonbase.load_sites(tmp_path / "sites_moon.csv")


def test_sensitivity_top_cells_and_reweight():
    df = pd.DataFrame({"score__t": [0.9, 0.8, 0.1, np.nan, 0.95, 0.7], "met__t": [1, 1, 1, 0, 1, 0]})
    excluded = np.array([False, False, False, False, True, False])
    # 0.1 is below the minimum score, NaN and met == 0 are not eligible, row 4 is near a known analog
    assert sensitivity.top_cells(df, "t", excluded, 10) == {0, 1}
    assert sensitivity.top_cells(df, "t", excluded, 1) == {0}
    t = {"features": {"a": {"value": 1, "sigma": 1, "weight": 2.0}, "b": {"value": 1, "sigma": 1}}}
    t2 = sensitivity.reweight(t, ["a", "b"], 0.8)
    assert t2["features"]["a"]["weight"] == pytest.approx(1.6) and t2["features"]["b"]["weight"] == pytest.approx(0.8)
    assert t["features"]["a"]["weight"] == 2.0, "the original target is left unchanged"
