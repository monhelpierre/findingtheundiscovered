"""API tests. Most use a tiny fixture export in a temp folder; one compares /api/score with the real
export (skipped when data/export/ doesn't exist)."""

import json
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings

from api import data
from api.models import CandidateNote, KnownAnalog, Target

TARGET = {
    "id": "moon_flat", "name": "Flat plain", "body": "moon", "category": "test", "description": "",
    "lat": None, "lon": None, "reference_url": "", "notes": "", "source_file": "test",
    "features": {
        "slope_mean": {"value": 5, "sigma": 3, "weight": 1.0},
        "relief": {"value": 40, "sigma": 30, "weight": 1.0},
        "albedo": {"value": 0.25, "sigma": 0.08, "weight": 1.0},
        "gentle_slope_frac": {"value": 0.95, "sigma": 0.1, "weight": 1.0, "mode": "atleast"},
        "ndvi": {"value": 0.1, "sigma": 0.06, "weight": 1.5, "mode": "atmost", "required": True},
    },
}
# 4 cells: a perfect match far from the analog, a perfect match inside the analog box, a steep cell,
# and a vegetated cell (fails the NDVI gate).
CELLS = {
    "cell": [0, 1, 2, 3],
    "lat": [10.0, 0.1, 10.5, 11.0],
    "lon": [10.0, 0.1, 10.5, 11.0],
    "slope_mean": [5, 5, 25, 5],
    "relief": [40, 40, 300, 40],
    "albedo": [0.25, 0.25, 0.25, 0.25],
    "gentle_slope_frac": [1.0, 1.0, 0.3, 1.0],
    "ndvi": [0.05, 0.05, 0.05, 0.6],
    "elev_mean": [500, 500, 900, 500],
}
INDEX = {
    "generated": "2026-11-14T00:00:00+00:00",
    "targets": [TARGET],
    "features": {}, "regions": [{"id": "test-region", "title": "Test", "bbox": [0, 0, 12, 12]}],
    "candidates": {"moon_flat": []}, "classifier": None,
    "known_analogs": [{"name": "Known site", "analog_type": "basaltic_volcanic",
                       "bbox": [0.0, 0.0, 0.2, 0.2], "lat": 0.1, "lon": 0.1}],
    "exclusion_km": 50.0,
}


class FixtureExport(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = Path(tempfile.mkdtemp(prefix="analog_api_"))
        (cls.tmp / "regions").mkdir()
        (cls.tmp / "index.json").write_text(json.dumps(INDEX))
        (cls.tmp / "regions" / "test-region.json").write_text(json.dumps({"id": "test-region", "columns": CELLS}))
        cls.override = override_settings(ANALOG_EXPORT_DIR=cls.tmp, TEAM_TOKEN="secret",
                                         ALLOWED_HOSTS=["testserver"])
        cls.override.enable()

    @classmethod
    def tearDownClass(cls):
        cls.override.disable()
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def score(self, **params):
        return self.client.get("/api/score", {"target": "moon_flat", "region": "test-region", **params})

    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["export"]["ok"])

    def test_index_uses_export_then_database(self):
        r = self.client.get("/api/index")
        self.assertEqual(r.json()["targets"][0]["id"], "moon_flat")
        self.assertIn("max-age", r["Cache-Control"])
        call_command("load_export", verbosity=0)
        Target.objects.filter(target_id="moon_flat").update(name="Edited in admin")
        self.assertEqual(self.client.get("/api/index").json()["targets"][0]["name"], "Edited in admin")
        self.assertEqual(KnownAnalog.objects.count(), 1)

    def test_region_and_bad_ids(self):
        self.assertEqual(self.client.get("/api/regions/test-region").status_code, 200)
        self.assertEqual(self.client.get("/api/regions/nope").status_code, 404)
        self.assertEqual(self.client.get("/api/regions/..%2F..%2Fsecrets").status_code, 404)

    def test_score_tiers_gate_and_exclusion(self):
        j = self.score().json()
        cols = j["columns"]
        self.assertAlmostEqual(cols["score"][0], 1.0, places=3)
        self.assertEqual(cols["tier"][0], "A")
        self.assertEqual(cols["tier"][3], None, "vegetated cell must fail the NDVI gate")
        self.assertEqual(cols["crit8"][2] is not None and cols["crit8"][2] < 0.7, True, "steep cell fails operations")
        self.assertTrue(cols["crit2"][2] < 0.7, "steep cell fails terrain (slope is in criterion 2)")
        cells = [c["cell"] for c in j["candidates"]]
        self.assertIn(0, cells)
        self.assertNotIn(1, cells, "a cell inside a known analog is never a candidate")
        self.assertNotIn(3, cells)

    def test_weights_change_the_score(self):
        base = self.score().json()["columns"]["score"][2]
        no_terrain = self.score(**{"w.slope_mean": "0", "w.relief": "0"}).json()["columns"]["score"][2]
        self.assertGreater(no_terrain, base)

    def test_bad_requests(self):
        self.assertEqual(self.score(**{"w.albedo": "99"}).status_code, 400)
        self.assertEqual(self.score(**{"w.albedo": "abc"}).status_code, 400)
        self.assertEqual(self.client.get("/api/score", {"target": "nope", "region": "test-region"}).status_code, 400)
        self.assertEqual(self.score(region="nope").status_code, 404)

    def test_notes_need_the_team_token(self):
        note = {"region": "test-region", "cell": 0, "lat": 10, "lon": 10, "author": "Ana", "text": "Looks good"}
        self.assertEqual(self.client.post("/api/notes", note, content_type="application/json").status_code, 403)
        r = self.client.post("/api/notes", note, content_type="application/json", HTTP_X_TEAM_TOKEN="wrong")
        self.assertEqual(r.status_code, 403)
        r = self.client.post("/api/notes", note, content_type="application/json", HTTP_X_TEAM_TOKEN="secret")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(CandidateNote.objects.count(), 1)
        got = self.client.get("/api/notes", {"region": "test-region", "cell": 0}).json()
        self.assertEqual(got[0]["text"], "Looks good")
        self.assertEqual(self.client.get("/api/notes", {"cell": "x"}).status_code, 400)

    def test_verdicts_are_summarised(self):
        post = lambda **kw: self.client.post(  # noqa: E731
            "/api/notes", {"region": "test-region", "cell": 0, "lat": 10, "lon": 10, "author": "Ana", "text": "", **kw},
            content_type="application/json", HTTP_X_TEAM_TOKEN="secret")
        self.assertEqual(post().status_code, 400, "a note needs a verdict or a text")
        self.assertEqual(post(verdict="maybe").status_code, 400)
        self.assertEqual(post(verdict="good").status_code, 201)
        self.assertEqual(post(verdict="good", text="Matches the imagery").status_code, 201)
        self.assertEqual(post(verdict="bad").status_code, 201)
        self.assertEqual(post(text="just a note").status_code, 201)
        got = self.client.get("/api/notes/summary").json()
        self.assertEqual(got, {"test-region:0": {"good": 2, "bad": 1}})


REAL_EXPORT = Path(settings.BASE_DIR).parent / "data" / "export"


class RealExport(TestCase):
    """With default weights, /api/score must reproduce `analog export` (the region files store features
    rounded to 3 decimals, so scores may differ by ~0.001)."""

    def setUp(self):
        if not (REAL_EXPORT / "index.json").exists():
            self.skipTest("no data/export")
        self.override = override_settings(ANALOG_EXPORT_DIR=REAL_EXPORT, ALLOWED_HOSTS=["testserver"])
        self.override.enable()
        self.addCleanup(self.override.disable)

    def test_same_scores_and_candidates_as_export(self):
        idx = data.index()
        tid = idx["targets"][0]["id"]
        rid = next(c["region"] for c in idx["candidates"][tid])
        j = self.client.get("/api/score", {"target": tid, "region": rid}).json()
        exported = data.region(rid)["columns"][f"score__{tid}"]
        for a, b in zip(exported, j["columns"]["score"], strict=True):
            self.assertEqual(a is None, b is None)
            if a is not None:
                self.assertAlmostEqual(a, b, delta=0.005)
        exp_c = [(c["cell"], c["tier"]) for c in idx["candidates"][tid] if c["region"] == rid]
        self.assertEqual(exp_c, [(c["cell"], c["tier"]) for c in j["candidates"]][:len(exp_c)])
