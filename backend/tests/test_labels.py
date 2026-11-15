"""The two known-analog files must describe the same sites:
data/known_analogs.csv (the team's list: point, analog_for, criteria_known_for) and
data/labels/known_analogs.geojson (what the pipeline uses: box, analog_type, same fields)."""

import csv
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "data" / "known_analogs.csv"
GEOJSON = ROOT / "data" / "labels" / "known_analogs.geojson"

pytestmark = pytest.mark.skipif(not (CSV.exists() and GEOJSON.exists()), reason="no label files")


def _load():
    rows = {r["name"]: r for r in csv.DictReader(CSV.open(encoding="utf8"))}
    feats = {f["properties"]["name"]: f for f in json.loads(GEOJSON.read_text(encoding="utf8"))["features"]}
    return rows, feats


def test_same_sites_in_both_files():
    rows, feats = _load()
    assert set(rows) == set(feats), f"only in CSV: {set(rows) - set(feats)}; only in GeoJSON: {set(feats) - set(rows)}"


def test_fields_agree_and_are_valid():
    rows, feats = _load()
    for name, row in rows.items():
        p = feats[name]["properties"]
        xs, ys = zip(*feats[name]["geometry"]["coordinates"][0], strict=True)
        lat, lon = float(row["lat"]), float(row["lon"])
        assert min(ys) <= lat <= max(ys) and min(xs) <= lon <= max(xs), f"{name}: CSV point outside its box"
        assert row["analog_for"] == p["analog_for"] and row["analog_for"] in {"Moon", "Mars"}, name
        crit = [int(c) for c in row["criteria_known_for"].split(";") if c]
        assert crit == p["criteria_known_for"] and crit and all(1 <= c <= 8 for c in crit), name
        assert p.get("analog_type"), f"{name}: needs an analog_type for the classifier"
