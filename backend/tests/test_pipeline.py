"""End-to-end test on a tiny synthetic region, in a temp ANALOG_ROOT."""

import importlib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parents[1]  # backend/ (holds config/)


@pytest.fixture()
def project(tmp_path, monkeypatch):
    shutil.copytree(BACKEND / "config", tmp_path / "config")
    monkeypatch.setenv("ANALOG_ROOT", str(tmp_path))
    import analog.paths
    importlib.reload(analog.paths)
    for m in ("earthref", "region", "features", "targets", "classifier", "export", "demo", "process", "crism",
              "importer", "fetch", "world", "cli"):
        importlib.reload(importlib.import_module(f"analog.{m}"))
    return tmp_path


def test_similarity_modes():
    import pandas as pd

    from analog.similarity import score_target
    df = pd.DataFrame({"a": [5.0, 7.0, np.nan], "b": [0.0, 1.0, 0.0]})
    t = {"features": {"a": {"value": 5, "sigma": 2}, "b": {"value": 0.5, "sigma": 0.1, "mode": "atmost"}}}
    s, cov = score_target(df, t)
    assert s[0] == pytest.approx(1.0)
    assert s[1] < 0.01
    # Only b available -> coverage 0.5, which still meets the default min_coverage.
    assert cov[2] == pytest.approx(0.5)
    assert s[2] == pytest.approx(1.0)


def test_demo_end_to_end(project):
    from analog import cli
    cli.main(["demo", "--train"])
    idx = json.loads((project / "data/export/index.json").read_text())
    assert idx["regions"][0]["id"] == "demo-synthetic"
    assert idx["classifier"] is not None
    reg = json.loads((project / "data/export/regions/demo-synthetic.json").read_text())
    cols = reg["columns"]
    assert len(cols["lon"]) == reg["n_cells"] > 3000
    for k in ("slope_mean", "roughness", "albedo", "min_sulfate", "score__mars_gale_sulfate", "analog_prob"):
        assert k in cols
    # The flat plain should beat the massif for Arcadia; the massif should win for Malapert.
    lon, lat = np.array(cols["lon"]), np.array(cols["lat"])
    a = np.array(cols["score__mars_arcadia_planitia"], dtype=float)
    m = np.array(cols["score__moon_malapert_massif"], dtype=float)
    plain = (lon < 0.15) & (lat < 0.15)
    massif = (lon > 0.45) & (lat > 0.45)
    assert np.nanmean(a[plain]) > np.nanmean(a[massif])
    assert np.nanmean(m[massif]) > np.nanmean(m[plain])


def test_ingest_custom_and_target_from_dem(project):
    from analog import cli
    cli.main(["demo"])
    dem = project / "data/regions/demo-synthetic/layers/dem.tif"
    cli.main(["ingest", "demo-synthetic", str(dem), "--kind", "custom:elev_copy"])
    cli.main(["target", "from-dem", "test_site", "--dem", str(dem), "--body", "moon", "--base", "moon_malapert_massif"])
    assert (project / "config/targets.d/test_site.yaml").exists()
    cli.main(["run", "demo-synthetic"])
    idx = json.loads((project / "data/export/index.json").read_text())
    assert "test_site" in [t["id"] for t in idx["targets"]]
    assert "elev_copy" in idx["features"]


def test_import_detection_and_footprints():
    from analog import importer, process
    P = Path
    assert importer.detect(P("N24W070.SRTMGL1.hgt.zip")) == "srtm"
    assert importer.detect(P("S25W071.hgt")) == "srtm"
    assert importer.detect(P("HLS.L30.T19JCN.2025030T144210.v2.0.B04.tif")) == "hls"
    assert importer.detect(P("HLS.S30.T19JCN.2025030T144210.v2.0.B8A.tif")) == "hls"
    assert importer.detect(P("EMIT_L2B_MIN_002_20240517T183930_2413812_004.nc")) == "emit"
    assert importer.detect(P("EMIT_L2B_MINUNCERT_002_20240517T183930.nc")) is None
    assert importer.detect(P("hrl000040ff_07_su183j_mtr3.lbl")) == "crism"
    assert importer.detect(P("anything.tif")) == "raster"
    assert importer.detect(P("notes.txt")) is None
    assert process.srtm_bounds("S25W071.hgt") == (-71, -25, -70, -24)
    assert process.srtm_bounds("N38W111.SRTMGL1.hgt.zip") == (-111, 38, -110, 39)
    assert importer.guess_kind(P("copernicus_dem_30m.tif")) == "dem"
    assert importer.guess_kind(P("site_ndvi_2025.tif")) == "ndvi"
    assert importer.guess_kind(P("mystery.tif")) is None
    groups = process.group_hls([P(f"HLS.L30.T19JCN.2025030T144210.v2.0.{b}.tif") for b in ("B02", "B04", "Fmask")])
    (sid, sc), = groups.items()
    assert sc["tile"] == "T19JCN" and set(sc["bands"]) == {"B02", "B04", "Fmask"}


def test_import_raster_into_region_and_target(project):
    from analog import cli
    cli.main(["demo"])
    dem = project / "data/regions/demo-synthetic/layers/dem.tif"
    renamed = project / "my_area_ndvi.tif"
    import shutil as _sh
    _sh.copy(dem, renamed)   # any GeoTIFF; the name says it's NDVI, so the kind is guessed
    cli.main(["import", str(renamed)])
    from analog.region import Region
    assert any(p.endswith("my_area_ndvi.tif") for p in Region.load("demo-synthetic").layers["ndvi"])
    # A planetary DEM clip -> target terrain, merged onto an existing target.
    cli.main(["import", str(dem), "--target", "moon_test", "--body", "moon", "--base", "moon_malapert_massif"])
    from analog.targets import load_targets
    t = load_targets()["moon_test"]
    assert t["body"] == "moon" and "slope_mean" in t["features"] and "ndvi" in t["features"]


def test_import_earth_engine_features(project):
    """An analogfeat_*.tif from backend/gee/analog_features.js (11 bands, fixed order) becomes one
    feature per band in the region it overlaps; re-importing replaces instead of duplicating."""
    import pandas as pd
    import rasterio
    from rasterio.transform import from_origin

    from analog import cli, process
    from analog.region import Region

    cli.main(["region", "create", "gee-site", "--bbox", "10", "10", "10.2", "10.2"])
    h = w = 20  # 0.01 deg cells, same grid as the region
    values = {name: np.full((h, w), float(i + 1), dtype="float32") for i, name in enumerate(process.GEE_BANDS)}
    values["ndvi"][:] = 0.05
    values["water_fraction"][:] = 0.0
    values["slope_mean"][:5, :] = np.nan   # missing data stays missing
    f = project / "analogfeat_gee-site.tif"
    with rasterio.open(f, "w", driver="GTiff", height=h, width=w, count=len(process.GEE_BANDS),
                       dtype="float32", crs="EPSG:4326", transform=from_origin(10, 10.2, 0.01, 0.01),
                       nodata=np.nan) as dst:
        for i, name in enumerate(process.GEE_BANDS, start=1):
            dst.write(values[name], i)

    from analog import importer
    assert importer.detect(f) == "gee"
    cli.main(["import", str(f), "--run"])
    cli.main(["import", str(f)])  # again: must replace, not add a second copy
    r = Region.load("gee-site")
    assert r.layers["custom:slope_mean"] == ["layers/gee_analogfeat_gee-site_slope_mean.tif"]
    assert set(r.layers) == set(process.GEE_KIND.values())

    df = pd.read_parquet(project / "data/regions/gee-site/features.parquet")
    assert len(df) == h * w
    assert abs(df["relief"].mean() - (process.GEE_BANDS.index("relief") + 1)) < 1e-6
    assert abs(df["temp_range"].dropna().mean() - (process.GEE_BANDS.index("temp_range") + 1)) < 1e-6
    assert df["slope_mean"].isna().sum() == 5 * w
    assert (df["water_fraction"] == 0).all() and np.allclose(df["ndvi"], 0.05)
    idx = json.loads((project / "data/export/index.json").read_text())
    assert any(reg["id"] == "gee-site" for reg in idx["regions"])


def test_meteomatics_offline_statistics_and_layers(project):
    """Cached monthly responses -> yearly statistics -> custom:<feature> layers (no request made)."""
    import pandas as pd

    from analog import cli, meteomatics
    from analog.region import Region

    cli.main(["region", "create", "mm-site", "--bbox", "10", "10", "10.1", "10.1"])
    r = Region.load("mm-site")
    spec, lats, lons = meteomatics.grid_spec(r.bbox, 0.05)
    assert spec == "10.1000,10.0000_10.0000,10.1000:0.05,0.05" and len(lats) == len(lons) == 3
    raw = r.dir / "raw" / "meteomatics"
    raw.mkdir(parents=True)
    daily = {"t_min_2m_24h:C": -5.0, "t_max_2m_24h:C": 15.0, "wind_speed_mean_10m_24h:ms": 4.0,
             "dust_aod_550nm:idx": 0.1, "soil_moisture_index_-5cm:idx": 0.2, "precip_24h:mm": 1.0,
             "evapotranspiration_24h:mm": 4.0, "snow_depth:cm": 0.0, "frost_depth:cm": -666}
    for m in range(1, 13):
        dates = [{"date": f"2024-{m:02d}-{d:02d}T00:00:00Z", "value": 0} for d in range(1, 31)]
        data = [{"parameter": p, "coordinates": [
            {"lat": float(la), "lon": float(lo),
             "dates": [{**d, "value": v + (m if p == "t_max_2m_24h:C" else 0)} for d in dates]}
            for la in lats for lo in lons]} for p, v in daily.items()]
        (raw / f"2024-{m:02d}_0.05.json").write_text(json.dumps({"data": data}))

    cli.main(["fetch", "meteomatics", "mm-site", "--offline"])
    cli.main(["features", "mm-site"])
    df = pd.read_parquet(r.dir / "features.parquet")
    assert np.allclose(df["tmin_k"], 268.15)                       # -5 C
    assert np.allclose(df["tyear_range_k"], (12 - 1) / 2)          # monthly mean rises by 0.5 K a month
    assert np.allclose(df["wind_ms"], 4.0) and np.allclose(df["dust_aod"], 0.1)
    assert np.allclose(df["aridity_index"], 0.25) and (df["ice_flag"] == 0).all()
    assert df["sun_elev_deg"].between(70, 80).all()                # ~10 N: high noon sun


def test_train_can_skip_features(project):
    import pandas as pd

    from analog import classifier
    df = pd.DataFrame({"label": ["a", None], "slope_mean": [1.0, 2.0], "wind_ms": [3.0, 4.0],
                       "cell": [0, 1], "lon": [0, 0], "lat": [0, 0], "row": [0, 0], "col": [0, 1]})
    assert classifier._feature_cols(df) == ["slope_mean", "wind_ms"]
    assert classifier._feature_cols(df, {"wind_ms"}) == ["slope_mean"]


def test_earth_engine_band_order_versions():
    from types import SimpleNamespace

    from analog import process
    assert process.gee_band_names(SimpleNamespace(descriptions=(), count=12, name="x.tif")) == process.GEE_BANDS
    assert process.gee_band_names(SimpleNamespace(descriptions=(), count=11, name="x.tif")) == process.GEE_BANDS[:11]
    with pytest.raises(SystemExit):
        process.gee_band_names(SimpleNamespace(descriptions=(), count=5, name="x.tif"))


def test_norm_ppf_matches_statistics():
    from statistics import NormalDist

    from analog.earthref import norm_ppf
    ps = np.array([0.001, 0.01, 0.024, 0.025, 0.2, 0.5, 0.8, 0.976, 0.999])
    expected = [NormalDist().inv_cdf(p) for p in ps]
    assert np.allclose(norm_ppf(ps), expected, atol=1e-8)


def test_percentile_features(project):
    """A target given as a percentile on its own world matches Earth cells at the same rank on Earth."""
    import pandas as pd

    from analog import earthref, targets
    from analog.similarity import score_all

    rng = np.random.default_rng(0)
    earth = rng.normal(280, 15, 20000)  # e.g. tmin_k over Earth's land
    earthref.write_reference({"tmin_k": (earth, np.ones_like(earth))}, "test")
    p05, p50 = np.percentile(earth, [5, 50])
    df = pd.DataFrame({"tmin_k": [p05, p50, np.nan], "slope_mean": [5.0, 5.0, 5.0]})
    t = {"body": "moon", "features": {"tmin_k": {"percentile": 0.05, "weight": 1},
                                      "slope_mean": {"value": 5, "sigma": 3}}}
    out = score_all(df, {"cold": t})
    assert out["rel__tmin_k"].iloc[0] == pytest.approx(earthref.norm_ppf(0.05), abs=0.03)
    assert out["score__cold"].iloc[0] == pytest.approx(1.0, abs=0.01)
    # median Earth vs a 5th-percentile target: 1.645 normal units apart -> exp(-1.645^2/2) ~ 0.26 on tmin_k
    assert out["crit3__cold"].iloc[1] == pytest.approx(np.exp(-0.5 * 1.645**2), abs=0.02)
    assert np.isnan(out["crit3__cold"].iloc[2])  # no value -> criterion not scored

    # Without a reference the feature is skipped (warning), not an error.
    earthref.reference_path().unlink()
    out = score_all(df, {"cold": t})
    assert "rel__tmin_k" not in out and "crit3__cold" in out and out["crit3__cold"].isna().all()

    with pytest.raises(SystemExit):
        targets.validate("bad", {"body": "moon", "features": {"tmin_k": {"percentile": 5}}}, "x.yaml")


def test_world_import_tiles_continent_file(project):
    """A whole-land Earth Engine file becomes 5 deg world tiles automatically (ocean tiles skipped), and the
    export lists their candidates separately, publishing only the tiles that hold one."""
    import json

    import rasterio
    from rasterio.transform import from_origin

    from analog import cli, process
    from analog.region import Region

    h, w, res = 100, 200, 0.05  # 10 x 5 deg: two 5 deg tiles, west = land, east = ocean
    vals = {
        "elev_mean": 300.0, "slope_mean": 4.0, "slope_p75": 6.0, "roughness": 8.0, "relief": 40.0,
        "albedo": 0.2, "ndvi": 0.05, "water_fraction": 0.0, "temp_range": 20.0, "clay_index": 1.2,
        "ferric_index": 1.5, "gentle_slope_frac": 0.97,
    }
    f = project / "analogfeat_earth_test.tif"
    with rasterio.open(f, "w", driver="GTiff", height=h, width=w, count=12, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(0, 50, res, res), nodata=np.nan) as dst:
        for i, name in enumerate(process.GEE_BANDS, start=1):
            a = np.full((h, w), vals[name], dtype="float32")
            if name == "water_fraction":
                a[:, 100:] = 1.0
            elif name != "ndvi":
                a[:, 100:] = np.nan
            dst.write(a, i)

    cli.main(["world", "import", str(f)])
    tiles = [r for r in Region.all() if r.scope == "world"]
    assert [r.id for r in tiles] == ["world-n45e000"]
    assert tiles[0].bbox == [0.0, 45.0, 5.0, 50.0] and tiles[0].cell_deg == res

    cli.main(["export"])
    idx = json.loads((project / "data/export/index.json").read_text())
    assert idx["world"]["tiles"] == 1 and "candidates_world" in idx
    published = {c["region"] for cs in idx["candidates_world"].values() for c in cs}
    files = {p.stem for p in (project / "data/export/regions").glob("world-*.json")}
    assert files == published  # only tiles holding a candidate are published
    assert all(r.get("scope") != "world" or r["id"] in published for r in idx["regions"])
    assert not (project / "data/export/.world_tiles").exists()
